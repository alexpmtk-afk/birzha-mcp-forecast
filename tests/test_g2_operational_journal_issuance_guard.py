"""Regression against falsely dating operational journal forecasts by candle end.

This uses inert spies; no HOME, network, DuckDB or market writes are made.
"""
from pathlib import Path
import pytest

from birzha.application.journal import ForecastJournalService
from birzha.application.prospective_capture import AdmissionRefused


class NoBuild:
    def __init__(self):
        self.calls = 0

    def build(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("unsafe event-time ForecastService.build reached")


class NoAppend:
    def __init__(self):
        self.calls = 0

    def append(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("journal append must not be reached")


@pytest.mark.parametrize("as_of_date", [None, "2026-10-10"])
def test_operational_forecast_create_refuses_before_fetch_and_journal_write(as_of_date):
    forecast, journal = NoBuild(), NoAppend()
    service = ForecastJournalService(forecasts=forecast, journal=journal)
    with pytest.raises(AdmissionRefused, match="UNATTESTED_FORECAST_ISSUANCE"):
        service.create_and_save("SBER", as_of_date=as_of_date)
    assert forecast.calls == 0
    assert journal.calls == 0


def test_operational_service_links_do_not_bypass_guard():
    root = Path(__file__).resolve().parents[1]
    mcp = (root / "src/birzha/mcp/server.py").read_text(encoding="utf-8")
    analysis = (root / "src/birzha/application/market_analysis.py").read_text(encoding="utf-8")
    assert "return _journal.create_and_save(symbol, as_of_date=as_of_date)" in mcp
    assert "saved = self.journal.create_and_save(symbol)" in analysis
    assert "UNATTESTED_FORECAST_ISSUANCE" in (
        root / "src/birzha/application/journal.py"
    ).read_text(encoding="utf-8")
    assert "return _forecast.build(symbol, as_of_date=as_of_date).to_dict()" in mcp


def test_existing_read_only_journal_functions_remain_supported():
    class ReadJournal:
        def get(self, forecast_id):
            return None

        def list_recent(self, *, limit, symbol):
            return []

        storage_scope = "TEST_ONLY"

    service = ForecastJournalService(forecasts=NoBuild(), journal=ReadJournal())
    assert service.get("unknown") is None
    assert service.list_recent(limit=3) == {
        "count": 0, "storage_scope": "TEST_ONLY", "forecasts": []
    }



def test_source_issuance_preflight_is_pure_blocked_and_not_a_future_promise():
    gate=ForecastJournalService(
        forecasts=NoBuild(),journal=NoAppend()).issuance_preflight()
    assert gate == {
        "schema": "G2_OPERATIONAL_ISSUANCE_PREFLIGHT_V1",
        "status": "BLOCKED",
        "reason": "UNATTESTED_FORECAST_ISSUANCE",
        "source_bound_live_issuer_verified": False,
        "canonical_write_authorized": False,
        "production_authorized": False,
    }


def test_analysis_run_core_abstains_before_any_six_market_history_or_forecast_write():
    from datetime import datetime, timezone
    from birzha.application.market_analysis import MarketAnalysisService
    from birzha.application.orchestration_plan import CORE_SYMBOLS

    class NoSync:
        calls=0
        def sync(self,*args,**kwargs):
            self.calls+=1
            raise AssertionError("D1 source sync must never be reached")

    history=NoSync()
    forecasts,journal=NoBuild(),NoAppend()
    operational=ForecastJournalService(forecasts=forecasts,journal=journal)
    result=MarketAnalysisService(history=history,journal=operational).run(
        now=datetime(2026,10,12,12,0,tzinfo=timezone.utc))
    assert result["status"]=="BLOCKED"
    assert result["reason"]=="UNATTESTED_FORECAST_ISSUANCE"
    assert result["requested"]==len(CORE_SYMBOLS)
    assert result["passed"]==0
    assert result["failed"]==0
    assert result["blocked"]==len(CORE_SYMBOLS)
    assert result["history_sync_attempted"] is False
    assert result["forecast_issuance_attempted"] is False
    assert result["production_authorized"] is False
    assert [x["symbol"] for x in result["items"]]==list(CORE_SYMBOLS)
    assert all(x["status"]=="BLOCKED" and x["stage"]=="issuance_preflight"
               for x in result["items"])
    assert history.calls==0 and forecasts.calls==0 and journal.calls==0


def test_operational_analysis_cannot_infer_forecast_permission_from_custom_positive_preflight():
    from birzha.application.market_analysis import MarketAnalysisService

    class ForgedApproval(ForecastJournalService):
        @staticmethod
        def issuance_preflight():
            return {"status":"PASS","canonical_write_authorized":True}

    class NoSync:
        def sync(self,*args,**kwargs):
            raise AssertionError("untrusted preflight must not reach source sync")

    blocked=ForgedApproval(forecasts=NoBuild(),journal=NoAppend())
    with pytest.raises(RuntimeError,match="separately reviewed"):
        MarketAnalysisService(history=NoSync(),journal=blocked).run()


def test_operational_analysis_mcp_description_does_not_promise_saving_six_forecasts():
    source=(Path(__file__).resolve().parents[1] /
            "src/birzha/mcp/server.py").read_text(encoding="utf-8")
    assert 'name="analysis.run_core"' in source
    assert "issuance preflight" in source
    assert "history sync" in source



def test_operational_outcome_refuses_before_any_future_market_or_database_io():
    from birzha.application.outcome import OutcomeService

    class ForbiddenMarket:
        @property
        def direct_resolver(self):
            raise AssertionError("live resolver must never be read")
        def candles_for_instrument(self,*args,**kwargs):
            raise AssertionError("future MOEX fetch must never be called")

    class ForbiddenJournal:
        def get(self,*args,**kwargs):
            raise AssertionError("forecast database must not be read")
        def list_for_forecast(self,*args,**kwargs):
            raise AssertionError("outcome database must not be read")
        def append(self,*args,**kwargs):
            raise AssertionError("outcome database must never be modified")

    store=ForbiddenJournal()
    service=OutcomeService(
        market_data=ForbiddenMarket(),forecasts=store,outcomes=store)
    for evaluation_date in (None,"2026-10-30"):
        result=service.operational_evaluate("fcst_future_not_certified",
                                             evaluation_date=evaluation_date)
        assert result["status"]=="BLOCKED"
        assert result["reason"]=="UNATTESTED_FUTURE_SOURCE_AND_SESSIONS"
        assert result["new_outcomes_written"]==0
        assert result["evaluated_future_sessions"]==0
        assert result["forecast_journal_read_attempted"] is False
        assert result["future_source_fetch_attempted"] is False
        assert result["outcome_journal_append_attempted"] is False
        assert result["official_session_calendar_verified"] is False
        assert result["independent_future_receipt_verified"] is False
        assert result["production_authorized"] is False


@pytest.mark.parametrize("forecast_id,evaluation_date",[
    ("",None),("   ",None),(None,None),(True,None),
    ("fcst",(123)),("fcst", "a"*35),
])
def test_operational_outcome_refuses_invalid_ids_dates_before_source_access(
    forecast_id,evaluation_date,
):
    from birzha.application.outcome import OutcomeService
    service=OutcomeService(market_data=NoBuild(),forecasts=NoBuild(),
                           outcomes=NoAppend())
    with pytest.raises(ValueError):
        service.operational_evaluate(forecast_id,evaluation_date=evaluation_date)


def test_mcp_outcome_tool_uses_operational_guard_instead_of_unsafe_legacy_evaluate():
    root=Path(__file__).resolve().parents[1]
    source=(root/"src/birzha/mcp/server.py").read_text(encoding="utf-8")
    assert 'name="outcome.evaluate"' in source
    assert "return _outcomes.operational_evaluate(" in source
    assert "return _outcomes.evaluate(" not in source
    assert "do not fetch future source or append" in source


def test_legacy_development_outcome_engine_still_exists_without_live_mcp_admission():
    from birzha.application.outcome import OutcomeService
    assert callable(OutcomeService.evaluate)
    assert "DEVELOPMENT-only" in OutcomeService.evaluate.__doc__
