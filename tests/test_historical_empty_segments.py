from datetime import date

import pytest

from birzha.application.historical_data import (
    D1_SESSION_VERIFICATION_VERSION,
    HistoricalDataIncompleteError,
    HistoricalDataService,
    ROLLING_HISTORY_VERIFICATION_VERSION,
)
from birzha.domain.market import Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


class _Market:
    direct_resolver = None


class _EmptySegmentsHistory(HistoricalDataService):
    def _segments(self, symbol, start, finish):
        return ()


def test_empty_historical_segments_never_create_verified_marker() -> None:
    store = DuckDBHistoricalCandleStore()
    service = _EmptySegmentsHistory(market_data=_Market(), store=store)  # type: ignore[arg-type]

    with pytest.raises(HistoricalDataIncompleteError, match="no segments"):
        service.sync(
            "SBER",
            timeframe="H1",
            from_date="2026-09-01",
            till_date="2026-09-02",
        )

    assert store.is_verified("SBER", "H1", "2026-09-01", "2026-09-02") is False
    store.close()


GOLD_FUTURE = Instrument(
    symbol="GOLD",
    secid="GDH5",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="GOLD",
)


class _EmptyCalendar:
    def __init__(self, provider):
        pass

    def dates(self, **kwargs):
        return ()


class _RootMarket:
    provider = object()
    direct_resolver = object()


class _OneEmptyRootSegment(HistoricalDataService):
    def _segments(self, symbol, start, finish):
        return ((GOLD_FUTURE, start, finish),)


def test_empty_rolling_contract_segment_never_creates_current_verification(monkeypatch) -> None:
    import birzha.application.historical_data as module

    monkeypatch.setattr(module, "MoexTradingCalendar", _EmptyCalendar)
    store = DuckDBHistoricalCandleStore()
    service = _OneEmptyRootSegment(  # type: ignore[arg-type]
        market_data=_RootMarket(), store=store
    )

    with pytest.raises(HistoricalDataIncompleteError, match="empty contract segments"):
        service.sync(
            "GOLD",
            timeframe="D1",
            from_date="2025-01-01",
            till_date="2025-01-10",
        )

    key = (
        f"GOLD#{ROLLING_HISTORY_VERIFICATION_VERSION}#"
        f"{D1_SESSION_VERIFICATION_VERSION}"
    )
    assert store.is_verified(key, "D1", "2025-01-01", "2025-01-10") is False
    assert store.is_session_range_verified(key, "2025-01-01", "2025-01-10") is False
    store.close()
