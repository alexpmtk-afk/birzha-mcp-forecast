"""Canonical persistence policy for BIRZHA market-price history.

Durable market-price history begins on 2021-01-01 and keeps completed D1 bars
forward without a rolling deletion window. H1 and M15 remain on-demand inputs
and must not be persisted in the shared/distributed historical database.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


PERSISTENT_PRICE_TIMEFRAMES = ("D1",)
ON_DEMAND_PRICE_TIMEFRAMES = ("H1", "M15")
D1_ARCHIVE_START = "2021-01-01"
MOEX_TIMEZONE = ZoneInfo("Europe/Moscow")
# Futures/evening trading may continue late. 23:55 MSK is a conservative
# boundary after which the current calendar day can be treated as completed.
D1_SAFE_COMPLETE_TIME = time(23, 55)


class IntradayPersistenceForbiddenError(ValueError):
    """Raised when code tries to put H1/M15 into durable price history."""


def normalize_timeframe(timeframe: str) -> str:
    value = timeframe.strip().upper()
    if not value:
        raise ValueError("timeframe must be non-empty")
    return value


def require_persistent_price_timeframe(timeframe: str) -> str:
    """Return normalized timeframe or fail closed for non-D1 persistence."""
    value = normalize_timeframe(timeframe)
    if value not in PERSISTENT_PRICE_TIMEFRAMES:
        raise IntradayPersistenceForbiddenError(
            f"persistent price history is D1-only; {value} must be fetched on demand"
        )
    return value


def require_persistent_price_timeframes(
    timeframes: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    if not timeframes:
        raise ValueError("at least one timeframe is required")
    return tuple(require_persistent_price_timeframe(item) for item in timeframes)


def latest_safe_d1_calendar_date(now: datetime | None = None) -> str:
    """Return a conservative upper calendar bound for completed MOEX D1 data.

    Before 23:55 MSK the current day may still be forming, so the previous
    calendar day is used. After that boundary the current Moscow calendar day
    is safe. Non-trading dates are harmless upper bounds: the MOEX session
    calendar still determines the last actual trading session stored.
    """
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local = current.astimezone(MOEX_TIMEZONE)
    day = local.date()
    if local.time().replace(tzinfo=None) < D1_SAFE_COMPLETE_TIME:
        day -= timedelta(days=1)
    return day.isoformat()
