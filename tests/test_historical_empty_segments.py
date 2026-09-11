import pytest

from birzha.application.historical_data import (
    HistoricalDataIncompleteError,
    HistoricalDataService,
)
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
