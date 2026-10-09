"""Regressions for migrated legacy base OHLC and append-only new versions."""
from __future__ import annotations

import json

from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore, _candle_revision

INSTRUMENT = Instrument(
    symbol="BR", secid="BRM5", root_symbol="BR",
    engine="futures", board="RFUD", market="forts", asset_class="future",
)


def _series(price: float) -> CandleSeries:
    candle = Candle(
        open=price, close=price, high=price+1, low=price-1,
        value=1000.0, volume=10.0,
        begin="2025-05-05T00:00:00", end="2025-05-05T23:59:59",
        completed=True, source="MOEX_ISS",
    )
    return CandleSeries(INSTRUMENT, "D1", (candle,))


def _base(store):
    return store._connection.execute(
        "SELECT close, revision, observed_at, source "
        "FROM historical_candles WHERE secid='BRM5'"
    ).fetchone()


def _revision_rows(store):
    return store._connection.execute(
        "SELECT revision, payload_json FROM historical_candle_revisions "
        "WHERE secid='BRM5' ORDER BY revision"
    ).fetchall()


def test_legacy_old_100_new_110_preserves_old_hash_and_new_distinct_version():
    store = DuckDBHistoricalCandleStore()
    try:
        old = _series(100.0)
        new = _series(110.0)
        old_hash, _ = _candle_revision(old.candles[0], "MOEX_ISS")
        new_hash, _ = _candle_revision(new.candles[0], "MOEX_ISS")
        assert old_hash != new_hash
        store.upsert_series(old)
        store._connection.execute(
            "UPDATE historical_candles SET revision=NULL, observed_at=NULL "
            "WHERE secid='BRM5'"
        )
        store.upsert_series(new)
        close, base_revision, first_seen, source = _base(store)
        assert close == 100.0
        assert base_revision == old_hash
        assert first_seen is None  # Must not invent old receipt time.
        assert source == "MOEX_ISS"
        versions = _revision_rows(store)
        assert len(versions) == 1
        assert versions[0][0] == new_hash
        assert json.loads(versions[0][1])["close"] == 110.0
    finally:
        store.close()


def test_legacy_identical_reingest_stamps_original_without_new_revision():
    store = DuckDBHistoricalCandleStore()
    try:
        old = _series(100.0)
        old_hash, _ = _candle_revision(old.candles[0], "MOEX_ISS")
        store.upsert_series(old)
        store._connection.execute(
            "UPDATE historical_candles SET revision=NULL, observed_at=NULL "
            "WHERE secid='BRM5'"
        )
        store.upsert_series(old)
        assert _base(store)[:3] == (100.0, old_hash, None)
        assert _revision_rows(store) == []
    finally:
        store.close()


def test_new_versions_still_append_without_overwriting_first_snapshot():
    store = DuckDBHistoricalCandleStore()
    try:
        first = _series(100.0)
        next_version = _series(110.0)
        base_hash, _ = _candle_revision(first.candles[0], "MOEX_ISS")
        store.upsert_series(first)
        first_seen = _base(store)[2]
        assert first_seen is not None
        store.upsert_series(next_version)
        assert _base(store)[:3] == (100.0, base_hash, first_seen)
        assert len(_revision_rows(store)) == 1
    finally:
        store.close()


def test_legacy_non_close_ohlc_revision_stays_separate():
    store = DuckDBHistoricalCandleStore()
    try:
        original = _series(100.0)
        old_hash, _ = _candle_revision(original.candles[0], "MOEX_ISS")
        from dataclasses import replace
        corrected = CandleSeries(
            INSTRUMENT, "D1", (
                replace(original.candles[0], high=103.0),
            ),
        )
        new_hash, _ = _candle_revision(corrected.candles[0], "MOEX_ISS")
        store.upsert_series(original)
        store._connection.execute(
            "UPDATE historical_candles SET revision=NULL, observed_at=NULL "
            "WHERE secid='BRM5'"
        )
        store.upsert_series(corrected)
        assert _base(store)[:3] == (100.0, old_hash, None)
        rows = _revision_rows(store)
        assert len(rows) == 1
        assert rows[0][0] == new_hash
        assert json.loads(rows[0][1])["high"] == 103.0
    finally:
        store.close()
