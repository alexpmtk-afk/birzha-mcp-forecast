"""Fail-closed readiness gate for durable D1 validation history.

H1 and M15 are deliberately excluded from durable readiness. They are fetched
on demand for each historical/current analysis window and are never required to
exist in the shared historical candle database. Durable readiness is also
clamped to the approved archive floor: it must never trigger pre-2021 backfill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from birzha.application.historical_data import HistoricalDataService
from birzha.application.history_policy import D1_ARCHIVE_START, PERSISTENT_PRICE_TIMEFRAMES


CORE_VALIDATION_SYMBOLS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
PRICE_LOOKBACK_DAYS = (("D1", 300),)


@dataclass(frozen=True, slots=True)
class ValidationHistoryRequirement:
    symbol: str
    timeframe: str
    from_date: str
    till_date: str
    verified: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "from_date": self.from_date,
            "till_date": self.till_date,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class ValidationDataReadinessReport:
    validation_start: str
    validation_end: str
    requirements: tuple[ValidationHistoryRequirement, ...]
    status: str

    @property
    def missing(self) -> tuple[ValidationHistoryRequirement, ...]:
        return tuple(item for item in self.requirements if not item.verified)

    def to_dict(self) -> dict[str, object]:
        return {
            "validation_start": self.validation_start,
            "validation_end": self.validation_end,
            "status": self.status,
            "archive_floor": D1_ARCHIVE_START,
            "persistent_timeframes": list(PERSISTENT_PRICE_TIMEFRAMES),
            "intraday_mode": "ON_DEMAND_NOT_PERSISTED",
            "required": len(self.requirements),
            "verified": len(self.requirements) - len(self.missing),
            "missing_count": len(self.missing),
            "requirements": [item.to_dict() for item in self.requirements],
        }


def required_price_ranges(
    validation_start: str, validation_end: str
) -> tuple[tuple[str, str, str], ...]:
    start = date.fromisoformat(validation_start[:10])
    end = date.fromisoformat(validation_end[:10])
    archive_floor = date.fromisoformat(D1_ARCHIVE_START)
    if start >= end:
        raise ValueError("validation_start must be before validation_end")
    return tuple(
        (
            timeframe,
            max(archive_floor, start - timedelta(days=lookback_days)).isoformat(),
            end.isoformat(),
        )
        for timeframe, lookback_days in PRICE_LOOKBACK_DAYS
    )


@dataclass(slots=True)
class ValidationDataReadinessService:
    history: HistoricalDataService

    def check(
        self,
        symbols: tuple[str, ...],
        *,
        validation_start: str,
        validation_end: str,
    ) -> ValidationDataReadinessReport:
        if not symbols:
            raise ValueError("at least one validation symbol is required")
        ranges = required_price_ranges(validation_start, validation_end)
        items = tuple(
            ValidationHistoryRequirement(
                symbol=symbol,
                timeframe=timeframe,
                from_date=from_date,
                till_date=till_date,
                verified=self.history.is_range_verified(
                    symbol,
                    timeframe=timeframe,
                    from_date=from_date,
                    till_date=till_date,
                ),
            )
            for symbol in symbols
            for timeframe, from_date, till_date in ranges
        )
        return ValidationDataReadinessReport(
            validation_start=validation_start[:10],
            validation_end=validation_end[:10],
            requirements=items,
            status="READY" if all(item.verified for item in items) else "NOT_READY",
        )
