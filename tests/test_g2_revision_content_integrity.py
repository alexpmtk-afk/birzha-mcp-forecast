"""Independent Stage-B negative review: immutable OHLC SHA and causal revision scope.

Only synthetic, in-memory DuckDB; never opens HOME or historic archive.
"""
from __future__ import annotations

from dataclasses import replace
import json
import pytest

from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore

INSTRUMENT = Instrument(
    symbol="BR", secid="BRM5", root_symbol="BR",
    board="RFUD", engine="futures", market="forts", asset_class="future",
)
DAY = "2025-05-05"


def _bar(*, close=100.0, seen="2025-05-06T08:00:00+03:00", revision=None):
    return Candle(
        open=99.0, close=close, high=close+2.0, low=98.0,
        value=1000.0, volume=10.0,
        begin="2025-05-05T00:00:00", end="2025-05-05T23:59:59",
        completed=True, source="MOEX_ISS", observed_at=seen, revision=revision,
    )


def _write(store, row):
    store.upsert_series(CandleSeries(INSTRUMENT,"D1",(row,)))


def _asof(store, stamp):
    return store.read_as_of(INSTRUMENT,"D1",DAY,DAY,knowledge_cutoff=stamp)


def test_modified_revision_payload_fails_asof_and_latest_with_hash_mismatch():
    store=DuckDBHistoricalCandleStore()
    try:
        _write(store,_bar(close=100.0))
        _write(store,_bar(close=110.0,seen="2025-05-07T08:00:00+03:00"))
        original=_asof(store,"2025-05-07T12:00:00+03:00")
        assert original.candles[0].close==110.0
        revision=store._connection.execute(
            "SELECT revision,payload_json FROM historical_candle_revisions "
            "WHERE secid='BRM5'").fetchone()
        assert revision is not None
        data=json.loads(revision[1])
        data["close"]=999.0
        store._connection.execute(
            "UPDATE historical_candle_revisions SET payload_json=? WHERE secid='BRM5'",
            [json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=False)])
        with pytest.raises(ValueError,match="SHA256 does not match"):
            _asof(store,"2025-05-07T12:00:00+03:00")
        with pytest.raises(ValueError,match="SHA256 does not match"):
            store.read_latest(INSTRUMENT,"D1",DAY,DAY)
        # Critical: a correction received LATER cannot poison earlier T0.
        earlier=_asof(store,"2025-05-06T12:00:00+03:00")
        assert earlier.candles[0].close==100.0
    finally:
        store.close()


def test_future_malformed_revision_does_not_poison_prior_cutoff():
    store=DuckDBHistoricalCandleStore()
    try:
        _write(store,_bar(close=100.0))
        _write(store,_bar(close=110.0,seen="2025-05-07T08:00:00+03:00"))
        store._connection.execute(
            "UPDATE historical_candle_revisions SET payload_json='{{invalid-json' "
            "WHERE secid='BRM5'")
        # Parsing evidence from the future is not allowed at an earlier T0.
        result=_asof(store,"2025-05-06T12:00:00+03:00")
        assert result.candles[0].close==100.0
        with pytest.raises(json.JSONDecodeError):
            _asof(store,"2025-05-07T12:00:00+03:00")
    finally:
        store.close()


def test_corrupted_content_addressed_base_rejected_in_asof():
    store=DuckDBHistoricalCandleStore()
    try:
        _write(store,_bar(close=100.0))
        store._connection.execute(
            "UPDATE historical_candles SET close=145.0 WHERE secid='BRM5'")
        with pytest.raises(ValueError,match="SHA256 does not match"):
            _asof(store,"2025-05-06T12:00:00+03:00")
    finally:
        store.close()


def test_opaque_provider_revision_token_is_not_strictly_content_attested():
    store=DuckDBHistoricalCandleStore()
    try:
        _write(store,_bar(close=100.0,revision="vendor-version-1"))
        assert store.read_latest(INSTRUMENT,"D1",DAY,DAY).candles[0].close==100.0
        # Existing permissive legacy/current-mode behavior is preserved.
        assert store.read(INSTRUMENT,"D1",DAY,DAY).candles[0].close==100.0
        # But no one may claim this opaque identifier is a content hash.
        with pytest.raises(ValueError,match="no verifiable content SHA256"):
            _asof(store,"2025-05-06T12:00:00+03:00")
    finally:
        store.close()


def test_legitimate_new_version_with_proven_sha_still_passes():
    store=DuckDBHistoricalCandleStore()
    try:
        _write(store,_bar(close=100.0))
        _write(store,_bar(close=110.0,seen="2025-05-07T08:00:00+03:00"))
        assert _asof(store,"2025-05-06T12:00:00+03:00").candles[0].close==100.0
        assert _asof(store,"2025-05-07T12:00:00+03:00").candles[0].close==110.0
        assert store.read_latest(INSTRUMENT,"D1",DAY,DAY).candles[0].close==110.0
    finally:
        store.close()
