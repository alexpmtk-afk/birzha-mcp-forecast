from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from birzha.application.market_analysis import MarketAnalysisService
from birzha.application.orchestration_plan import CORE_SYMBOLS


class History:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str]] = []

    def sync(self, symbol: str, *, timeframe: str, from_date: str, till_date: str):
        self.calls.append((symbol, timeframe, from_date, till_date))
        if symbol == "BR":
            raise TimeoutError("provider unavailable")
        return SimpleNamespace(fetched_candles=2, reused_verified_range=False)


class Journal:
    def __init__(self, history: History) -> None:
        self.history = history
        self.calls: list[str] = []

    def create_and_save(self, symbol: str):
        assert any(call[0] == symbol for call in self.history.calls)
        self.calls.append(symbol)
        if symbol == "GOLD":
            raise TimeoutError("forecast unavailable")
        return {
            "forecast": {"symbol": symbol, "direction": "UP", "validation_status": "UNVALIDATED_BASELINE"},
            "journal": {"created": True},
        }


def test_analysis_updates_recent_daily_history_before_forecast_and_reports_each_failure() -> None:
    history = History()
    journal = Journal(history)
    service = MarketAnalysisService(history=history, journal=journal)  # type: ignore[arg-type]

    report = service.run(now=datetime(2026, 9, 24, 19, 0, tzinfo=UTC))

    assert report["status"] == "PARTIAL"
    assert report["passed"] == 4
    assert report["failed"] == 2
    assert report["recent_history_till"] == "2026-09-23"
    assert report["full_archive_verified"] is False
    assert report["model_accuracy_confirmed"] is False
    assert tuple(call[0] for call in history.calls) == CORE_SYMBOLS
    assert all(call[1] == "D1" for call in history.calls)
    assert all(call[2] == "2026-08-24" and call[3] == "2026-09-23" for call in history.calls)
    assert journal.calls == ["SBER", "Si", "GOLD", "IMOEX", "RTSI"]
    assert report["items"][2]["stage"] == "history"
    assert report["items"][3]["stage"] == "forecast"
