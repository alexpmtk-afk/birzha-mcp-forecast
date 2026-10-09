"""Explicit decision-time forecast inputs: no future versions, no future root selection.

Synthetic in-memory DB; these tests do not certify a historical archive's
independently attested vintage or forecast predictive quality.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from birzha.application.forecast import ForecastService
from birzha.application.historical_data import HistoricalDataService
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.stored_market_data import StoredMarketDataView
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore

SECID = "BRM5"
INSTRUMENT = Instrument(
    symbol="BR", secid=SECID, root_symbol="BR",
    board="RFUD", engine="futures", market="forts",
    asset_class="future", data_capabilities=("CANDLES",),
)
DATE = "2025-05-06"
EARLY = "2025-05-06T09:00:00+03:00"
LATE = "2025-05-06T11:00:00+03:00"


def _feed(store):
    for timeframe, begin, end in (
        ("D1", "2025-05-05T10:00:00", "2025-05-05T23:49:59"),
        ("H1", "2025-05-06T07:00:00", "2025-05-06T07:59:59"),
        ("M15", "2025-05-06T08:00:00", "2025-05-06T08:14:59"),
    ):
        one = Candle(
            open=99.0, close=100.0, high=102.0, low=98.0,
            value=1000.0, volume=10.0, begin=begin, end=end,
            completed=True, source="MOEX_ISS",
            observed_at="2025-05-06T08:30:00+03:00",
        )
        correction = Candle(
            open=99.0, close=110.0, high=112.0, low=98.0,
            value=1100.0, volume=11.0, begin=begin, end=end,
            completed=True, source="MOEX_ISS",
            observed_at="2025-05-06T10:00:00+03:00",
        )
        store.upsert_series(CandleSeries(INSTRUMENT, timeframe, (one,)))
        store.upsert_series(CandleSeries(INSTRUMENT, timeframe, (correction,)))


def _forecast_services(store, *, view="as_of", flow=None, require=True):
    history = HistoricalDataService(market_data=object(), store=store)
    market_data = StoredMarketDataView(
        base=object(), history=history,
        version_view=view, require_stored_resolution=require,
    )
    snapshot_service = MarketSnapshotService(market_data=market_data, flow=flow)
    return snapshot_service, ForecastService(snapshots=snapshot_service)


def test_one_cutoff_controls_three_timeframes_and_forecast_t0():
    store = DuckDBHistoricalCandleStore()
    try:
        _feed(store)
        snapshots, forecasts = _forecast_services(store)
        early = snapshots.build(SECID, knowledge_cutoff_at=EARLY)
        later = snapshots.build(SECID, knowledge_cutoff_at=LATE)

        assert early.as_of == EARLY
        assert later.as_of == LATE
        assert (early.d1.last_close, early.h1.last_close, early.m15.last_close) == (
            100.0, 100.0, 100.0,
        )
        assert (later.d1.last_close, later.h1.last_close, later.m15.last_close) == (
            110.0, 110.0, 110.0,
        )
        forecast = forecasts.build(SECID, knowledge_cutoff_at=EARLY)
        assert forecast.created_at_t0 == EARLY
        assert forecast.secid == SECID
        assert forecast.reference_price == 100.0
        assert store.read(INSTRUMENT, "D1", "2025-05-05", "2025-05-05").candles[0].close == 100.0
    finally:
        store.close()


def test_root_future_is_rejected_even_with_stored_session_mapping():
    store = DuckDBHistoricalCandleStore()
    try:
        _feed(store)
        snapshots, _ = _forecast_services(store)
        with pytest.raises(ValueError, match="exact SECID"):
            snapshots.build("BR", knowledge_cutoff_at=EARLY)
    finally:
        store.close()


def test_strict_cutoff_rejects_unproven_market_source_and_flow():
    store = DuckDBHistoricalCandleStore()
    try:
        _feed(store)
        with pytest.raises(ValueError, match="stored market data"):
            MarketSnapshotService(market_data=object()).build(
                SECID, knowledge_cutoff_at=EARLY
            )
        snapshots, _ = _forecast_services(store, view="first_seen")
        with pytest.raises(ValueError, match="as_of version view"):
            snapshots.build(SECID, knowledge_cutoff_at=EARLY)
        snapshots, _ = _forecast_services(store, require=False)
        with pytest.raises(ValueError, match="frozen resolution"):
            snapshots.build(SECID, knowledge_cutoff_at=EARLY)
        snapshots, _ = _forecast_services(store, flow=object())
        with pytest.raises(ValueError, match="flow receipt provenance"):
            snapshots.build(SECID, knowledge_cutoff_at=EARLY)
    finally:
        store.close()


@pytest.mark.parametrize("bad_cutoff", [
    "2025-05-06T09:00:00",
    "not-a-time",
])
def test_strict_cutoff_needs_timezone_and_valid_iso(bad_cutoff):
    store = DuckDBHistoricalCandleStore()
    try:
        snapshots, _ = _forecast_services(store)
        with pytest.raises(ValueError, match="cutoff"):
            snapshots.build(SECID, knowledge_cutoff_at=bad_cutoff)
    finally:
        store.close()


def test_strict_cutoff_rejects_mismatched_date_or_missing_exact_contract():
    store = DuckDBHistoricalCandleStore()
    try:
        _feed(store)
        snapshots, _ = _forecast_services(store)
        with pytest.raises(ValueError, match="as_of_date"):
            snapshots.build(
                SECID, as_of_date="2025-05-05", knowledge_cutoff_at=EARLY,
            )
        with pytest.raises(ValueError, match="not stored"):
            snapshots.build("BRN5", knowledge_cutoff_at=EARLY)
    finally:
        store.close()


def test_strict_cutoff_excludes_unobserved_late_version():
    store = DuckDBHistoricalCandleStore()
    try:
        # A late D1 payload alone does not satisfy three timeframe inputs.
        candle = Candle(
            open=100.0, close=101.0, high=102.0, low=99.0,
            value=1000.0, volume=10.0,
            begin="2025-05-05T10:00:00", end="2025-05-05T23:49:59",
            completed=True, observed_at="2025-05-06T10:00:00+03:00",
            source="MOEX_ISS",
        )
        store.upsert_series(CandleSeries(INSTRUMENT, "D1", (candle,)))
        snapshots, _ = _forecast_services(store)
        with pytest.raises(ValueError, match="no completed candles"):
            snapshots.build(SECID, knowledge_cutoff_at=EARLY)
    finally:
        store.close()
