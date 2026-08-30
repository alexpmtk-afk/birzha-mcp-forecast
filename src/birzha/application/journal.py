"""Application service for creating and retrieving immutable forecasts."""

from __future__ import annotations

from dataclasses import dataclass

from birzha.application.forecast import ForecastService
from birzha.storage.forecast_journal import DuckDBForecastJournal


@dataclass(slots=True)
class ForecastJournalService:
    forecasts: ForecastService
    journal: DuckDBForecastJournal

    def create_and_save(self, symbol: str, *, as_of_date: str | None = None) -> dict[str, object]:
        record = self.forecasts.build(symbol, as_of_date=as_of_date)
        append = self.journal.append(record)
        return {
            "forecast": record.to_dict(),
            "journal": append.to_dict(),
            "storage_scope": self.journal.storage_scope,
        }

    def get(self, forecast_id: str) -> dict[str, object] | None:
        record = self.journal.get(forecast_id)
        return record.to_dict() if record else None

    def list_recent(self, *, limit: int = 20, symbol: str | None = None) -> dict[str, object]:
        records = self.journal.list_recent(limit=limit, symbol=symbol)
        return {
            "count": len(records),
            "storage_scope": self.journal.storage_scope,
            "forecasts": [record.to_dict() for record in records],
        }
