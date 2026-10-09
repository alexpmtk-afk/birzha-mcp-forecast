"""Exact candle finality, current projection and historical first receipt tests.

Only in-memory DuckDB; never changes the HOME/production archive.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


INSTRUMENT = Instrument(
    symbol="BR", secid="BRM5", board="RFUD", engine="futures",
    market="forts", asset_class="future", root_symbol="BR",
)
DAY = "2025-05-05"
BEGIN = "2025-05-05T00:00:00"
END = "2025-05-05T23:59:59"


def _bar(*, close=100.0, completed=False, observed_at=None):
    return Candle(
        open=99.0, close=close, high=close + 2.0, low=98.0,
        value=1000.0, volume=10.0, begin=BEGIN, end=END,
        completed=completed, source="MOEX_ISS",
        observed_at=observed_at,
    )


def _write(store, candle):
    store.upsert_series(CandleSeries(INSTRUMENT, "D1", (candle,)))


def _get(store):
    return store.read_latest(INSTRUMENT, "D1", DAY, DAY)


def _at(store, instant):
    return store.read_as_of(
        INSTRUMENT, "D1", DAY, DAY, knowledge_cutoff=instant,
    )


def test_forming_to_completed_latest_projection_and_consistent_coverage():
    store = DuckDBHistoricalCandleStore()
    try:
        _write(store, _bar(observed_at="2025-05-05T15:00:00+03:00"))
        assert _get(store).count == 1
        assert _get(store).candles[0].completed is False
        assert store.coverage_latest("BRM5", "D1").count == 0
        assert store.stored_trade_dates_latest("BRM5", "D1", DAY, DAY) == ()

        _write(store, _bar(
            close=105.0, completed=True,
            observed_at="2025-05-06T08:00:00+03:00",
        ))
        # Existing first-seen reader and readiness are unchanged.
        assert store.read(INSTRUMENT, "D1", DAY, DAY).candles[0].close == 100.0
        assert store.coverage("BRM5", "D1").count == 0
        current = _get(store)
        assert current.count == 1
        assert current.candles[0].completed is True
        assert current.candles[0].close == 105.0
        assert current.candles[0].observed_at == "2025-05-06T08:00:00+03:00"
        assert store.coverage_latest("BRM5", "D1").count == 1
        assert store.stored_trade_dates_latest("BRM5", "D1", DAY, DAY) == (DAY,)

        # Historical cutoff sees the forming revision before its final
        # version arrived; the final price cannot contaminate yesterday.
        before = _at(store, "2025-05-05T16:00:00+03:00")
        assert before.count == 1
        assert before.candles[0].completed is False
        assert before.candles[0].close == 100.0
        assert _at(store, "2025-05-06T09:00:00+03:00").candles[0].close == 105.0
        base = store._connection.execute(
            "SELECT close, completed FROM historical_candles WHERE secid='BRM5'"
        ).fetchone()
        assert base == (100.0, False)  # The immutable base survives.
    finally:
        store.close()


def test_later_stale_forming_does_not_downgrade_final_version():
    store = DuckDBHistoricalCandleStore()
    try:
        _write(store, _bar(completed=True, close=105.0,
                           observed_at="2025-05-06T08:00:00+03:00"))
        _write(store, _bar(completed=False, close=101.0,
                           observed_at="2025-05-06T09:00:00+03:00"))
        assert _get(store).candles[0].close == 105.0
        assert _get(store).candles[0].completed is True
        assert store.coverage_latest("BRM5", "D1").count == 1
        assert _at(store, "2025-05-06T10:00:00+03:00").candles[0].close == 105.0
    finally:
        store.close()


def test_new_completed_correction_does_not_leak_to_earlier_t0():
    store = DuckDBHistoricalCandleStore()
    try:
        first = _bar(completed=True, close=105.0,
                     observed_at="2025-05-06T08:00:00+03:00")
        _write(store, first)
        _write(store, replace(first, close=109.0, high=111.0,
                              observed_at="2025-05-07T08:00:00+03:00"))
        assert _get(store).candles[0].close == 109.0
        old = _at(store, "2025-05-06T12:00:00+03:00")
        assert old.count == 1
        assert old.candles[0].close == 105.0
        assert _at(store, "2025-05-07T12:00:00+03:00").candles[0].close == 109.0
    finally:
        store.close()


def test_legacy_unknown_receipt_is_not_promoted_into_historical_asof():
    store = DuckDBHistoricalCandleStore()
    try:
        _write(store, _bar(completed=True, close=100.0))
        store._connection.execute(
            "UPDATE historical_candles SET observed_at=NULL, revision=NULL "
            "WHERE secid='BRM5'"
        )
        assert _get(store).candles[0].close == 100.0
        assert _at(store, "2025-05-07T00:00:00+03:00").count == 0
        _write(store, _bar(completed=True, close=106.0,
                           observed_at="2025-05-08T10:00:00+03:00"))
        assert _at(store, "2025-05-07T00:00:00+03:00").count == 0
        assert _at(store, "2025-05-09T00:00:00+03:00").candles[0].close == 106.0
    finally:
        store.close()


def test_strict_asof_refuses_unzoned_cutoff_and_future_completed_end():
    store = DuckDBHistoricalCandleStore()
    try:
        _write(store, _bar(
            completed=True, close=105.0,
            observed_at="2025-05-05T14:00:00+03:00",
        ))
        with pytest.raises(ValueError, match="explicit timezone"):
            _at(store, "2025-05-05T20:00:00")
        # A falsely prematurely marked completed version may not be used
        # prior to the actual event/end time, even if already received.
        early = _at(store, "2025-05-05T18:00:00+03:00")
        assert early.count == 0
        assert _at(store, "2025-05-06T01:00:00+03:00").count == 1
    finally:
        store.close()


def test_asof_fails_closed_for_two_versions_with_identical_receipt_time():
    store = DuckDBHistoricalCandleStore()
    try:
        _write(store, _bar(
            completed=True, close=100.0,
            observed_at="2025-05-06T08:00:00+03:00",
        ))
        _write(store, _bar(
            completed=True, close=110.0,
            observed_at="2025-05-06T08:00:00+03:00",
        ))
        with pytest.raises(ValueError, match="ambiguous same-timestamp"):
            _at(store, "2025-05-06T09:00:00+03:00")
    finally:
        store.close()
