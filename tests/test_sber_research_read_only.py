"""Contract tests for the read-only SBER research consumer.

All fixtures are disposable DuckDB files in pytest tmp_path, never the HOME
history/state DB or a live MOEX request.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.sber_research_read_only import SberResearchReadOnly
from birzha.domain.market import CandleSeries
from test_research_intraday_archive import (
    bar, hourly, instrument, minute_rows,
)
from birzha.application.research_intraday_fetch import aggregate_strict_m15


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_archive(tmp_path, *, gap=False, checkpoint=True):
    path = tmp_path / "research" / "SBER" / "test.duckdb"
    path.parent.mkdir(parents=True)
    store = ResearchIntradayArchive(str(path))
    try:
        hourly_series = hourly()
        if gap:
            at = datetime.fromisoformat("2025-06-09T12:00:00+03:00")
            second = bar(at, 3599)
            hourly_series = replace(
                hourly_series,
                candles=hourly_series.candles + (second,)
            )
        store.ingest(hourly_series)
        minutes = minute_rows()
        m15, _ = aggregate_strict_m15(minutes, "2025-06-09")
        if gap:
            at = datetime.fromisoformat("2025-06-09T10:30:00+03:00")
            separated = tuple(
                bar(at + timedelta(minutes=i), 59, value=130+i)
                for i in range(15)
            )
            appended, _ = aggregate_strict_m15(
                replace(minutes, candles=separated), "2025-06-09"
            )
            m15 = replace(m15, candles=m15.candles + appended.candles)
        store.ingest(m15)
        if checkpoint:
            store.mark_captured(
                instrument(), "2025-06-09",
                h1_rows=len(hourly_series.candles),
                m15_rows=len(m15.candles),
                raw_m1_rows=15 if not gap else 30,
                incomplete_m15_buckets=1,
            )
    finally:
        store.close()
    return path


def test_research_reader_is_sha_pinned_read_only_and_does_not_change_bytes(tmp_path):
    db = make_archive(tmp_path)
    original = db.read_bytes()
    h = digest(db)
    with SberResearchReadOnly(db, expected_sha256=h) as reader:
        assert reader.inventory() == {"H1": 1, "M15": 1}
        first = reader.read(
            timeframe="H1", from_civil_date="2025-06-09",
            through_civil_date="2025-06-09"
        )
        again = reader.read(
            timeframe="H1", from_civil_date="2025-06-09",
            through_civil_date="2025-06-09"
        )
        assert first == again
        assert first.research_only is True
        assert first.forecast_eligible is False
        assert first.point_in_time_verified is False
        assert first.session_calendar_verified is False
        assert first.capture_days == (("2025-06-09", "CAPTURED_WITH_GAPS"),)
        candle, = first.bars
        assert (candle.open, candle.high, candle.low, candle.close) == (
            100.0, 102.0, 99.0, 101.0
        )
        assert candle.trade_session_date is None
        assert candle.session_id is None
        assert candle.available_at is None
        assert candle.observed_at is None
        assert candle.revision is None
        assert candle.available_at_confidence == "UNKNOWN"
        assert candle.forecast_eligible is False
        assert candle.source_kind == "MOEX_NATIVE_H1"
        assert candle.source_fingerprint
        assert candle.civil_date == "2025-06-09"
        quarter = reader.read(
            timeframe="M15", from_civil_date="2025-06-09",
            through_civil_date="2025-06-09"
        )
        assert quarter.bars[0].source_kind == "MOEX_M1_AGGREGATED_M15"
        assert quarter.bars[0].value == 15000
        assert quarter.gap_candidates == ()
    assert db.read_bytes() == original
    assert not (db.parent / (db.name + ".wal")).exists()
    with pytest.raises(ValueError, match="closed"):
        reader.inventory()


def test_reader_flags_intervening_unknown_bucket_without_claiming_missing_trade(tmp_path):
    db = make_archive(tmp_path, gap=True)
    with SberResearchReadOnly(db, expected_sha256=digest(db)) as reader:
        records = reader.read(
            timeframe="M15", from_civil_date="2025-06-09",
            through_civil_date="2025-06-09"
        )
        assert len(records.bars) == 2
        assert records.gap_candidates == ((
            "2025-06-09", "2025-06-09T10:14:59+03:00",
            "2025-06-09T10:30:00+03:00",
        ),)
        # A candidate is NOT a proven missing trade or a fake candle.
        assert all(b.forecast_eligible is False for b in records.bars)


@pytest.mark.parametrize("scope", [
    dict(timeframe="D1", from_civil_date="2025-06-09", through_civil_date="2025-06-09"),
    dict(timeframe="M1", from_civil_date="2025-06-09", through_civil_date="2025-06-09"),
    dict(timeframe="H1", from_civil_date="2024-12-31", through_civil_date="2025-06-09"),
    dict(timeframe="H1", from_civil_date="2025-06-09", through_civil_date="2027-01-01"),
    dict(timeframe="H1", from_civil_date="2025-06-10", through_civil_date="2025-06-09"),
    dict(timeframe="H1", from_civil_date="2025-6-9", through_civil_date="2025-06-09"),
])
def test_invalid_scopes_fail_closed(scope, tmp_path):
    db = make_archive(tmp_path)
    with SberResearchReadOnly(db, expected_sha256=digest(db)) as reader:
        with pytest.raises(ValueError):
            reader.read(**scope)


def test_read_bounded_no_silent_limit_or_empty_scope(tmp_path):
    db = make_archive(tmp_path, gap=True)
    with SberResearchReadOnly(db, expected_sha256=digest(db)) as reader:
        with pytest.raises(ValueError, match="max_bars"):
            reader.read(timeframe="H1", from_civil_date="2025-06-09",
                        through_civil_date="2025-06-09", max_bars=0)
        with pytest.raises(ValueError, match="exceeds max_bars"):
            reader.read(timeframe="M15", from_civil_date="2025-06-09",
                        through_civil_date="2025-06-09", max_bars=1)
        with pytest.raises(ValueError, match="no captured"):
            reader.read(timeframe="M15", from_civil_date="2025-06-10",
                        through_civil_date="2025-06-10")


def test_wrong_checksum_or_tampering_is_rejected(tmp_path):
    db = make_archive(tmp_path)
    before = digest(db)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        SberResearchReadOnly(db, expected_sha256="0"*64)
    with SberResearchReadOnly(db, expected_sha256=before) as reader:
        assert reader.inventory()["H1"] == 1
        # Force an actual byte change AFTER opening the read-only handle;
        # the next read must detect it before issuing its query.
        with db.open("ab") as w:
            w.write(b"externally-mutated-after-readonly-open")
        with pytest.raises(ValueError, match="SHA256 mismatch"):
            reader.inventory()


def test_reader_rejects_unmarked_db_and_missing_checkpoint(tmp_path):
    db = tmp_path / "production.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE d1_prices (x INT)")
    with pytest.raises(ValueError, match="mandatory research"):
        SberResearchReadOnly(db, expected_sha256=digest(db))
    original = db.read_bytes()
    assert db.read_bytes() == original
    orphan = make_archive(tmp_path / "other", checkpoint=False)
    with pytest.raises(ValueError, match="without immutable day"):
        SberResearchReadOnly(orphan, expected_sha256=digest(orphan))


def test_reader_rejects_wrong_market_and_cross_instrument_mix(tmp_path):
    db = make_archive(tmp_path)
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE research_intraday_candles SET board='NOT_TQBR' WHERE timeframe='H1'")
    with pytest.raises(ValueError, match="wrong-board"):
        SberResearchReadOnly(db, expected_sha256=digest(db))


def test_reader_rejects_symlink_and_missing_file(tmp_path):
    db = make_archive(tmp_path)
    alias = tmp_path / "alias.duckdb"
    try:
        alias.symlink_to(db)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported by this test environment")
    with pytest.raises(ValueError, match="non-symlink"):
        SberResearchReadOnly(alias, expected_sha256=digest(db))
    with pytest.raises(ValueError, match="non-symlink"):
        SberResearchReadOnly(tmp_path / "absent.duckdb", expected_sha256=digest(db))


def test_reader_rejects_quality_mismatch_without_overwriting_file(tmp_path):
    db = make_archive(tmp_path)
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE research_intraday_captured_days SET quality='INVALID' ")
    before = db.read_bytes()
    with pytest.raises(ValueError, match="capture identity/quality"):
        SberResearchReadOnly(db, expected_sha256=digest(db))
    assert db.read_bytes() == before
