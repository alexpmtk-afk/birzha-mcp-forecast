from datetime import date
from types import SimpleNamespace

import pytest

import scripts.g2_imoex_calendar_coverage_audit as module
from birzha.domain.market import Candle, CandleSeries, Instrument


def _instrument():
    return Instrument(symbol="IMOEX", secid="IMOEX", board="SNDX",
                      engine="stock", market="index", asset_class="index")


def _candle(day):
    return Candle(open=3000., close=3010., high=3020., low=2990.,
                  volume=0, value=0, begin=f"{day} 00:00:00",
                  end=f"{day} 23:59:59", completed=True)


class FakeSource:
    provider = object()

    def __init__(self, dates):
        self.dates = dates
        self.calls = []

    def resolve(self, symbol, *, as_of):
        self.calls.append(("resolve", symbol, as_of))
        return _instrument()

    def candles_for_instrument(self, instrument, *, timeframe,
                               from_date, till_date, completed_only):
        self.calls.append(("D1", timeframe, from_date, till_date, completed_only))
        return CandleSeries(instrument=instrument, timeframe="D1",
                            candles=tuple(_candle(d) for d in self.dates))


def test_calendar_audit_reports_exact_matching_days_without_claiming_pit(monkeypatch):
    days = ["2025-01-15", "2025-01-16"]
    source = FakeSource(days)
    class Calendar:
        def __init__(self, provider):
            assert provider is source.provider
        def dates(self, **kwargs):
            assert kwargs["security"] == "IMOEX"
            return tuple(date.fromisoformat(d) for d in days)
    monkeypatch.setattr(module, "MoexTradingCalendar", Calendar)
    report = module.audit(source, "2025-01-01", "2025-01-31")
    assert report["state"] == "SOURCE_CALENDAR_CONSISTENT_NOT_PIT_VERIFIED"
    assert report["history_activity_sessions"] == report["native_completed_d1_sessions"] == 2
    assert report["independent_calendar_proven"] is False
    assert report["no_intraday_prices_downloaded"] and report["no_db_write"]
    assert [item[0] for item in source.calls] == ["resolve", "D1"]


def test_calendar_disagreement_reports_not_verified(monkeypatch):
    source = FakeSource(["2025-01-15"])
    class Calendar:
        def __init__(self, provider):
            pass
        def dates(self, **kwargs):
            return (date(2025, 1, 15), date(2025, 1, 16))
    monkeypatch.setattr(module, "MoexTradingCalendar", Calendar)
    report = module.audit(source, "2025-01-01", "2025-01-31")
    assert report["state"] == "CALENDAR_NOT_VERIFIED"
    assert report["activity_without_d1_sample"] == ["2025-01-16"]


def test_cli_plan_is_network_and_write_free(monkeypatch, capsys):
    monkeypatch.setattr(module.MarketDataService, "default",
                        lambda: pytest.fail("offline plan touched network"))
    assert module.main(["--mode", "plan", "--from-date", "2025-01-01",
                        "--till-date", "2025-12-31"]) == 0
    assert "PLAN_NO_NETWORK_NO_WRITE" in capsys.readouterr().out


@pytest.mark.parametrize("dates", [
    ["2024-01-01", "2024-02-01"],
    ["2025-01-01", "2027-01-01"],
    ["2026-01-01", "2025-01-01"],
])
def test_cli_rejects_forbidden_or_reversed_period_before_network(monkeypatch, dates):
    monkeypatch.setattr(module.MarketDataService, "default",
                        lambda: pytest.fail("forbidden period touched network"))
    with pytest.raises(SystemExit) as err:
        module.main(["--mode", "audit",
                     "--from-date", dates[0], "--till-date", dates[1]])
    assert err.value.code == 2
