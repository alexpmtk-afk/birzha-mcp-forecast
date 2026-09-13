"""Canonical persistence policy for BIRZHA market-price history.

Only completed D1 candles belong to the durable historical database. Intraday
H1 and M15 are contextual analysis inputs and must be fetched on demand. M15 is
constructed by the MOEX provider from M1 for the requested short window.
"""

from __future__ import annotations


PERSISTENT_PRICE_TIMEFRAMES = ("D1",)
ON_DEMAND_PRICE_TIMEFRAMES = ("H1", "M15")


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


def require_persistent_price_timeframes(timeframes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if not timeframes:
        raise ValueError("at least one timeframe is required")
    return tuple(require_persistent_price_timeframe(item) for item in timeframes)
