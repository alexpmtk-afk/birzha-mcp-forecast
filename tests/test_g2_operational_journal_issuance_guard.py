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
