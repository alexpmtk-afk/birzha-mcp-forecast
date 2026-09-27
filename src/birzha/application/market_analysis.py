"""One-request current market review for the six configured instruments.

The current daily window is verified before a forecast is saved. This bounded
operation does not certify the entire research archive or the model's accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from birzha.application.historical_data import HistoricalDataService
from birzha.application.history_policy import D1_ARCHIVE_START, latest_safe_d1_calendar_date
from birzha.application.journal import ForecastJournalService
from birzha.application.orchestration_plan import CORE_SYMBOLS


RECENT_ARCHIVE_DAYS = 31
DIRECTION_RU = {
    "UP": "возможен рост",
    "DOWN": "возможно снижение",
    "NEUTRAL": "явного направления нет",
}


@dataclass(slots=True)
class MarketAnalysisService:
    history: HistoricalDataService
    journal: ForecastJournalService

    def run(self, *, now: datetime | None = None) -> dict[str, object]:
        observed = now or datetime.now(UTC)
        safe_till = latest_safe_d1_calendar_date(observed)
        recent_from = max(
            D1_ARCHIVE_START,
            (datetime.fromisoformat(safe_till).date() - timedelta(days=RECENT_ARCHIVE_DAYS - 1)).isoformat(),
        )
        items: list[dict[str, object]] = []
        for symbol in CORE_SYMBOLS:
            try:
                sync = self.history.sync(
                    symbol,
                    timeframe="D1",
                    from_date=recent_from,
                    till_date=safe_till,
                )
            except Exception as exc:
                items.append({
                    "symbol": symbol,
                    "status": "ERROR",
                    "stage": "history",
                    "message_ru": "Не удалось проверить и обновить дневную историю.",
                    "error_type": type(exc).__name__,
                })
                continue

            try:
                saved = self.journal.create_and_save(symbol)
                forecast = saved["forecast"]
                assert isinstance(forecast, dict)
                direction = str(forecast.get("direction", ""))
                items.append({
                    "symbol": symbol,
                    "status": "PASS",
                    "history": {
                        "from_date": recent_from,
                        "till_date": safe_till,
                        "fetched_candles": sync.fetched_candles,
                        "reused_verified_range": sync.reused_verified_range,
                    },
                    "forecast": forecast,
                    "journal": saved["journal"],
                    "message_ru": (
                        f"{symbol}: {DIRECTION_RU.get(direction, 'направление не определено')}; "
                        "прогноз предварительный, точность модели ещё не подтверждена."
                    ),
                })
            except Exception as exc:
                items.append({
                    "symbol": symbol,
                    "status": "ERROR",
                    "stage": "forecast",
                    "history": {
                        "from_date": recent_from,
                        "till_date": safe_till,
                        "fetched_candles": sync.fetched_candles,
                        "reused_verified_range": sync.reused_verified_range,
                    },
                    "message_ru": "Дневная история обновлена, но прогноз получить не удалось.",
                    "error_type": type(exc).__name__,
                })

        passed = sum(item["status"] == "PASS" for item in items)
        status = "PASS" if passed == len(CORE_SYMBOLS) else "ERROR" if passed == 0 else "PARTIAL"
        return {
            "status": status,
            "requested": len(CORE_SYMBOLS),
            "passed": passed,
            "failed": len(CORE_SYMBOLS) - passed,
            "recent_history_from": recent_from,
            "recent_history_till": safe_till,
            "full_archive_verified": False,
            "model_accuracy_confirmed": False,
            "message_ru": (
                f"Подготовлены предварительные прогнозы для {passed} из {len(CORE_SYMBOLS)} инструментов. "
                "Проверена и обновлена недавняя дневная история; полнота архива с 2021 года "
                "и точность прогнозов пока не подтверждены."
            ),
            "items": items,
        }
