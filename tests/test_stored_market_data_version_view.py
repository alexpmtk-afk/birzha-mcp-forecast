"""StoredMarketDataView explicit first-seen/latest/observed-as-of routing."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from birzha.application.historical_data import HistoricalDataService
from birzha.application.stored_market_data import StoredMarketDataView
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore

INS = Instrument(
    symbol="BR", secid="BRM5", board="RFUD", engine="futures",
    market="forts", asset_class="future", root_symbol="BR",
)


def _view(store, mode):
    history = HistoricalDataService(market_data=object(), store=store)
    return StoredMarketDataView(
        base=object(), history=history, version_view=mode,
    )


def _read(view, now=None, *, completed_only=True):
    return view.candles_for_instrument(
        INS, timeframe="D1",
        from_date="2025-05-05", till_date="2025-05-05",
        completed_only=completed_only, now=now,
    )


def test_explicit_latest_view_observes_completed_while_default_preserves_first_seen():
    store = DuckDBHistoricalCandleStore()
    try:
        first = Candle(99, 100, 102, 98, 1000, 10,
                       "2025-05-05T00:00:00", "2025-05-05T23:59:59",
                       False, observed_at="2025-05-05T15:00:00+03:00")
        final = Candle(99, 105, 107, 98, 1200, 11,
                       first.begin, first.end, True,
                       observed_at="2025-05-06T08:00:00+03:00")
        store.upsert_series(CandleSeries(INS, "D1", (first,)))
        store.upsert_series(CandleSeries(INS, "D1", (final,)))
        assert _read(_view(store, "first_seen")).count == 0
        latest = _read(_view(store, "latest"))
        assert latest.count == 1
        assert latest.candles[0].close == 105
        assert _read(_view(store, "as_of"), datetime.fromisoformat(
            "2025-05-05T16:00:00+03:00")).count == 0
        assert _read(_view(store, "as_of"), datetime.fromisoformat(
            "2025-05-05T16:00:00+03:00"), completed_only=False).candles[0].close == 100
        assert _read(_view(store, "as_of"), datetime.fromisoformat(
            "2025-05-06T09:00:00+03:00")).candles[0].close == 105
    finally:
        store.close()


def test_asof_view_requires_explicit_zoned_cutoff():
    store = DuckDBHistoricalCandleStore()
    try:
        view = _view(store, "as_of")
        with pytest.raises(ValueError, match="timezone-aware"):
            _read(view)
        with pytest.raises(ValueError, match="timezone-aware"):
            _read(view, datetime(2025, 5, 6))
    finally:
        store.close()


def test_unsupported_view_backend_fails_closed():
    store = DuckDBHistoricalCandleStore()
    try:
        view = _view(store, "impossible")
        with pytest.raises(ValueError, match="unsupported version_view"):
            _read(view)
    finally:
        store.close()
