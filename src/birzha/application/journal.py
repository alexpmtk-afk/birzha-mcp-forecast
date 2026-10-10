"""Application service for creating and retrieving immutable forecasts."""

from __future__ import annotations

from dataclasses import dataclass

from birzha.application.forecast import ForecastService
from birzha.application.prospective_capture import AdmissionRefused
from birzha.storage.forecast_journal import DuckDBForecastJournal


@dataclass(slots=True)
class ForecastJournalService:
    forecasts: ForecastService
    journal: DuckDBForecastJournal

    def create_and_save(self, symbol: str, *, as_of_date: str | None = None) -> dict[str, object]:
        """Refuse unsafe operational issuance before fetching or writing.

        ForecastService.build() preserves candle EVENT time as T0, not the
        verified provider receipt/issuer time. Both forecast.create and
        MarketAnalysisService used this method; until an attested source-
        bound LIVE writer is approved, no new journal row is admissible.
        The read-only forecast.build preview and historical journal reads
        remain supported. The prospective source-bound STAGING adapter has
        its own explicit capture path and is deliberately not routed here.
        """
        raise AdmissionRefused(
            "UNATTESTED_FORECAST_ISSUANCE: journal write refused; "
            "ForecastService event-time T0 is not first-receipt/issued-at T0. "
            "Use forecast.build only for an unpersisted baseline preview; "
            "source-bound prospective capture remains staging-only."
        )

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
