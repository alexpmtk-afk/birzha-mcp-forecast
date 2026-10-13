"""Fail-closed, read-only pre-install audit for authentic IMOEX V2 research data.

This never opens HOME production DuckDB and never writes market data. The only
output is a small receipt outside the immutable source artifact directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import duckdb

SOURCE_SHA = "e66262c6d809e7a81b2b91d6e1068e60b3dac114b0310c862d05127c36b29a5c"
DB_NAME = "birzha_intraday_research_2025_2026_source_compatible.duckdb"
META_NAME = "IMOEX_2025-2026_ACTUAL_SOURCE_INTERVALS_V2.json"
SUMS_NAME = "IMOEX_2025-2026_SOURCE_COMPAT_V2_SHA256SUMS.txt"
COUNTS = {"H1": (4199, 541), "M15": (16073, 1296)}
EXPECTED_DAYS = 457
EXPECTED_FROM, EXPECTED_TO = "2025-01-03", "2026-10-12"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _regular(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError("expected regular, non-symlinked source file: " + path.name)


def _one(conn, sql: str):
    return conn.execute(sql).fetchone()[0]


def verify(source_dir: Path) -> dict:
    src = source_dir.resolve(strict=True)
    db, meta_path, sums_path = [src / x for x in (DB_NAME, META_NAME, SUMS_NAME)]
    for p in (db, meta_path, sums_path):
        _regular(p)
    rows = [line.split("  ", 1) for line in sums_path.read_text("utf-8").splitlines()]
    if any(len(row) != 2 for row in rows) or len(rows) != 2:
        raise ValueError("incorrect SHA manifest")
    sums = {filename: digest for digest, filename in rows}
    if set(sums) != {DB_NAME, META_NAME}:
        raise ValueError("unexpected SHA filenames")
    digest = sha256(db)
    if digest != SOURCE_SHA or sums[DB_NAME] != digest or sums[META_NAME] != sha256(meta_path):
        raise ValueError("source bytes do not match the pinned research version")
    meta = json.loads(meta_path.read_text("utf-8"))
    if (meta.get("new_v2_db_sha256") != digest
        or meta.get("schema") != "IMOEX_RESEARCH_CANONICAL_PLUS_OBSERVED_PARTIAL_SOURCE_V2"
        or meta.get("future_prediction_pit_attested") is not False
        or meta.get("full_session_completeness_attested") is not False
        or meta.get("minutes_M1_persisted") is not False
        or meta.get("no_trade") is not True
        or meta.get("release_blocked") is not True):
        raise ValueError("research quality/identity markers conflict")
    for tf, expected in COUNTS.items():
        key = "total_observed_" + tf.lower()
        if meta.get(key) != sum(expected):
            raise ValueError("research manifest count mismatch " + tf)
    if meta.get("source_dates") != EXPECTED_DAYS:
        raise ValueError("research date count mismatch")

    con = duckdb.connect(str(db), read_only=True)
    try:
        marker = con.execute(
            "SELECT value FROM research_intraday_meta "
            "WHERE key='source_compatible_exception_extension'"
        ).fetchone()
        if marker != ("MOEX_SOURCE_OBSERVED_V1",):
            raise ValueError("not the verified source-observed V2 research database")
        strict = dict(con.execute(
            "SELECT timeframe,count(*) FROM research_intraday_candles "
            "WHERE secid='IMOEX' GROUP BY timeframe"
        ).fetchall())
        extras = dict(con.execute(
            "SELECT timeframe,count(*) FROM research_intraday_source_observed_exceptions "
            "WHERE secid='IMOEX' GROUP BY timeframe"
        ).fetchall())
        if set(strict) != set(COUNTS) or set(extras) != set(COUNTS):
            raise ValueError("unexpected timeframe")
        for tf,(s,e) in COUNTS.items():
            if strict[tf] != s or extras[tf] != e:
                raise ValueError("count mismatch " + tf)
        if _one(con, "SELECT count(*) FROM research_intraday_candles WHERE secid<>'IMOEX'") \
           or _one(con, "SELECT count(*) FROM research_intraday_source_observed_exceptions WHERE secid<>'IMOEX'"):
            raise ValueError("unexpected instrument")
        date_stats = con.execute(
            "SELECT count(*),min(trade_date),max(trade_date) "
            "FROM research_intraday_captured_days WHERE secid='IMOEX'"
        ).fetchone()
        if date_stats != (EXPECTED_DAYS, EXPECTED_FROM, EXPECTED_TO):
            raise ValueError("date coverage mismatch")
        # Keep strict bars and observed-but-short bars separate; do not upgrade quality.
        quality = dict(con.execute(
            "SELECT quality,count(*) FROM research_intraday_source_observed_exceptions "
            "GROUP BY quality"
        ).fetchall())
        if quality != {"NATIVE_H1_SHORT_OBSERVED": 541,
                       "SOURCE_M15_WITH_FEWER_THAN_15_M1": 1296}:
            raise ValueError("source quality mismatch")
        bad_m15 = _one(con, """
            SELECT count(*) FROM research_intraday_source_observed_exceptions
            WHERE timeframe='M15' AND
            (source_minute_rows IS NULL OR source_minute_rows < 1 OR source_minute_rows > 14)
        """)
        if bad_m15:
            raise ValueError("source partial M15 has invalid observed minute count")
        duplicates = _one(con, """
            SELECT count(*) FROM (
              SELECT timeframe,begin FROM (
                SELECT timeframe,begin FROM research_intraday_candles
                UNION ALL
                SELECT timeframe,begin FROM research_intraday_source_observed_exceptions
              ) v GROUP BY timeframe,begin HAVING count(*)<>1
            )
        """)
        if duplicates:
            raise ValueError("duplicate H1/M15 interval")
        overlaps = _one(con, """
            WITH candle_intervals AS (
              SELECT timeframe,begin,end_time FROM research_intraday_candles
              UNION ALL
              SELECT timeframe,begin,end_time FROM research_intraday_source_observed_exceptions
            ), s AS (
              SELECT timeframe,begin,end_time,
              lag(end_time) OVER (PARTITION BY timeframe ORDER BY begin) last_end
              FROM candle_intervals
            )
            SELECT count(*) FROM s WHERE last_end IS NOT NULL AND begin < last_end
        """)
        if overlaps:
            raise ValueError("overlapping intervals")
        invalid = _one(con, """
            SELECT count(*) FROM (
              SELECT open,high,low,close,volume,value FROM research_intraday_candles
              UNION ALL
              SELECT open,high,low,close,volume,value FROM research_intraday_source_observed_exceptions
            ) v WHERE NOT (
              isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close)
              AND low>0 AND low<=least(open,close)
              AND greatest(open,close)<=high
              AND (volume IS NULL OR (isfinite(volume) AND volume>=0))
              AND (value IS NULL OR (isfinite(value) AND value>=0))
            )
        """)
        if invalid:
            raise ValueError("bad OHLCV rows")
    finally:
        con.close()

    return {
        "schema": "IMOEX_HOME_RESEARCH_READONLY_PREFLIGHT_V1",
        "status": "PASS_STAGING_READONLY__HOME_NOT_TESTED",
        "source_sha256": digest,
        "source_dates": date_stats[0],
        "first_source_date": date_stats[1],
        "last_source_date": date_stats[2],
        "counts": {tf: {"strict": strict[tf], "source_partial": extras[tf],
                         "total": strict[tf]+extras[tf]} for tf in COUNTS},
        "quality": quality,
        "raw_m1_stored": False,
        "strict_historical_PIT": False,
        "complete_session_calendar_verified": False,
        "research_only": True,
        "release_blocked": True,
        "trade_allowed": False,
        "home_installed": False,
        "home_history_state_db_touched": False,
        "proof": "pinned hash and all source rows checked read-only; no HOME operation"
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_dir.resolve(strict=True)
    receipt = args.receipt.resolve()
    if receipt.parent == source or source in receipt.parents:
        parser.error("receipt must live outside the original source directory")
    if not receipt.parent.is_dir() or receipt.exists() or receipt.is_symlink():
        parser.error("receipt parent must exist and file must not already exist")
    data = verify(source)
    receipt.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("IMOEX_HOME_RESEARCH_READONLY_PREFLIGHT=PASS")
    print(json.dumps({"source_dates": data["source_dates"], "counts": data["counts"],
                      "release_blocked": data["release_blocked"]}, sort_keys=True))


if __name__ == "__main__":
    main()
