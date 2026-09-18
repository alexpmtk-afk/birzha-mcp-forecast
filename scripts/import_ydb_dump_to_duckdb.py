#!/usr/bin/env python3
"""Import a YDB tools-dump into the single-host DuckDB runtime.

YDB tools dump writes URL-encoded CSV rows without a header. This importer maps
only BIRZHA application tables. Distributed rate-slot history is intentionally
not imported because the local runtime has one process-local upstream gate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
from pathlib import Path
from urllib.parse import unquote_plus

import duckdb


def _decode_row(row: list[str], expected: int, table: str) -> list[str]:
    if len(row) != expected:
        raise RuntimeError(f"{table}: expected {expected} columns, got {len(row)}")
    return [unquote_plus(value) for value in row]


def _rows(path: Path, expected: int, table: str):
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.reader(handle):
            if not raw:
                continue
            yield _decode_row(raw, expected, table)


def _fresh(path: Path) -> Path:
    tmp = path.with_suffix(path.suffix + ".new")
    tmp.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    return tmp


def _replace(tmp: Path, final: Path) -> None:
    backup = final.with_suffix(final.suffix + ".pre-migration.bak")
    if final.exists() and not backup.exists():
        shutil.copy2(final, backup)
    os.replace(tmp, final)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _init_state(path: Path) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect(str(path))
    db.execute("""
        CREATE TABLE forecast_records (
            forecast_id VARCHAR PRIMARY KEY,
            payload_hash VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            secid VARCHAR NOT NULL,
            created_at_t0 VARCHAR NOT NULL,
            engine_version VARCHAR NOT NULL,
            inserted_at TIMESTAMP DEFAULT current_timestamp
        )
    """)
    db.execute("""
        CREATE TABLE outcome_records (
            outcome_id VARCHAR PRIMARY KEY,
            forecast_id VARCHAR NOT NULL,
            horizon_sessions INTEGER NOT NULL,
            payload_hash VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            inserted_at TIMESTAMP DEFAULT current_timestamp
        )
    """)
    return db


def _init_history(path: Path) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect(str(path))
    db.execute("""
        CREATE TABLE historical_candles (
            secid VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            root_symbol VARCHAR,
            board VARCHAR NOT NULL,
            engine VARCHAR NOT NULL,
            market VARCHAR NOT NULL,
            asset_class VARCHAR NOT NULL,
            timeframe VARCHAR NOT NULL,
            begin VARCHAR NOT NULL,
            end_time VARCHAR NOT NULL,
            open DOUBLE,
            close DOUBLE,
            high DOUBLE,
            low DOUBLE,
            value DOUBLE,
            volume DOUBLE,
            completed BOOLEAN NOT NULL,
            source VARCHAR NOT NULL,
            PRIMARY KEY (secid, timeframe, begin)
        )
    """)
    db.execute("""
        CREATE TABLE historical_verified_ranges (
            symbol VARCHAR NOT NULL,
            timeframe VARCHAR NOT NULL,
            from_date VARCHAR NOT NULL,
            till_date VARCHAR NOT NULL,
            PRIMARY KEY (symbol, timeframe, from_date, till_date)
        )
    """)
    db.execute("""
        CREATE TABLE historical_sessions (
            symbol VARCHAR NOT NULL,
            secid VARCHAR NOT NULL,
            trade_date VARCHAR NOT NULL,
            PRIMARY KEY (symbol, secid, trade_date)
        )
    """)
    db.execute("""
        CREATE TABLE historical_session_verified_ranges (
            symbol VARCHAR NOT NULL,
            from_date VARCHAR NOT NULL,
            till_date VARCHAR NOT NULL,
            PRIMARY KEY (symbol, from_date, till_date)
        )
    """)
    db.execute("""
        CREATE TABLE historical_flow_rows (
            dataset VARCHAR NOT NULL,
            key_symbol VARCHAR NOT NULL,
            row_key VARCHAR NOT NULL,
            trade_date VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            PRIMARY KEY (dataset, key_symbol, row_key)
        )
    """)
    db.execute("""
        CREATE TABLE historical_flow_verified (
            dataset VARCHAR NOT NULL,
            key_symbol VARCHAR NOT NULL,
            from_date VARCHAR NOT NULL,
            till_date VARCHAR NOT NULL,
            PRIMARY KEY (dataset, key_symbol, from_date, till_date)
        )
    """)
    return db


def _init_orchestration(path: Path) -> duckdb.DuckDBPyConnection:
    db = duckdb.connect(str(path))
    db.execute("""
        CREATE TABLE orchestration_runs (
            workflow_id VARCHAR PRIMARY KEY,
            kind VARCHAR NOT NULL,
            stage VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            created_at VARCHAR NOT NULL,
            updated_at VARCHAR NOT NULL,
            metadata_json VARCHAR NOT NULL,
            last_error VARCHAR NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE orchestration_actions (
            action_id VARCHAR PRIMARY KEY,
            workflow_id VARCHAR NOT NULL,
            stage VARCHAR NOT NULL,
            kind VARCHAR NOT NULL,
            sequence BIGINT NOT NULL,
            payload_json VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            attempt BIGINT NOT NULL,
            max_attempts BIGINT NOT NULL,
            lease_owner VARCHAR NOT NULL,
            lease_until VARCHAR NOT NULL,
            evidence_json VARCHAR NOT NULL,
            last_error VARCHAR NOT NULL
        )
    """)
    return db


def _batched_insert(db: duckdb.DuckDBPyConnection, sql: str, rows, *, batch_size: int = 5000) -> int:
    batch: list[list[object]] = []
    count = 0
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            db.executemany(sql, batch)
            count += len(batch)
            batch.clear()
    if batch:
        db.executemany(sql, batch)
        count += len(batch)
    return count


def _import_state(root: Path, db: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    counts["forecast_records"] = _batched_insert(
        db,
        """INSERT INTO forecast_records
        (forecast_id,payload_hash,payload_json,symbol,secid,created_at_t0,engine_version)
        VALUES (?,?,?,?,?,?,?)""",
        _rows(root / "forecast_records" / "data_00.csv", 7, "forecast_records"),
    )
    outcome_rows = (
        [row[0], row[1], int(row[2]), row[3], row[4]]
        for row in _rows(root / "outcome_records" / "data_00.csv", 5, "outcome_records")
    )
    counts["outcome_records"] = _batched_insert(
        db,
        """INSERT INTO outcome_records
        (outcome_id,forecast_id,horizon_sessions,payload_hash,payload_json)
        VALUES (?,?,?,?,?)""",
        outcome_rows,
    )
    return counts


def _candle_rows(root: Path):
    path = root / "historical_candles" / "data_00.csv"
    for row in _rows(path, 6, "historical_candles"):
        payload = json.loads(row[4])
        instrument = payload["instrument"]
        candle = payload["candle"]
        yield [
            row[0],
            str(instrument["symbol"]),
            instrument.get("root_symbol"),
            str(instrument["board"]),
            str(instrument["engine"]),
            str(instrument["market"]),
            str(instrument["asset_class"]),
            row[1],
            row[2],
            row[3],
            candle.get("open"),
            candle.get("close"),
            candle.get("high"),
            candle.get("low"),
            candle.get("value"),
            candle.get("volume"),
            bool(candle["completed"]),
            row[5],
        ]


def _import_history(root: Path, db: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    counts["historical_candles"] = _batched_insert(
        db,
        """INSERT INTO historical_candles
        (secid,symbol,root_symbol,board,engine,market,asset_class,timeframe,
         begin,end_time,open,close,high,low,value,volume,completed,source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        _candle_rows(root),
    )
    mappings = [
        (
            "historical_candles_verified_ranges",
            "historical_verified_ranges",
            4,
            """INSERT INTO historical_verified_ranges
            (symbol,timeframe,from_date,till_date) VALUES (?,?,?,?)""",
        ),
        (
            "historical_candles_sessions",
            "historical_sessions",
            3,
            """INSERT INTO historical_sessions
            (symbol,secid,trade_date) VALUES (?,?,?)""",
        ),
        (
            "historical_candles_session_verified_ranges",
            "historical_session_verified_ranges",
            3,
            """INSERT INTO historical_session_verified_ranges
            (symbol,from_date,till_date) VALUES (?,?,?)""",
        ),
        (
            "historical_flow_rows",
            "historical_flow_rows",
            6,
            """INSERT INTO historical_flow_rows
            (dataset,key_symbol,row_key,trade_date,payload_json,source)
            VALUES (?,?,?,?,?,?)""",
        ),
        (
            "historical_flow_rows_verified",
            "historical_flow_verified",
            4,
            """INSERT INTO historical_flow_verified
            (dataset,key_symbol,from_date,till_date) VALUES (?,?,?,?)""",
        ),
    ]
    for source, target, width, sql in mappings:
        counts[target] = _batched_insert(
            db,
            sql,
            _rows(root / source / "data_00.csv", width, source),
        )
    return counts


def _import_orchestration(root: Path, db: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    counts["orchestration_runs"] = _batched_insert(
        db,
        """INSERT INTO orchestration_runs
        (workflow_id,kind,stage,status,created_at,updated_at,metadata_json,last_error)
        VALUES (?,?,?,?,?,?,?,?)""",
        _rows(root / "orchestration_runs" / "data_00.csv", 8, "orchestration_runs"),
    )
    actions = (
        [
            row[0], row[1], row[2], row[3], int(row[4]), row[5], row[6],
            int(row[7]), int(row[8]), row[9], row[10], row[11], row[12],
        ]
        for row in _rows(root / "orchestration_actions" / "data_00.csv", 13, "orchestration_actions")
    )
    counts["orchestration_actions"] = _batched_insert(
        db,
        """INSERT INTO orchestration_actions
        (action_id,workflow_id,stage,kind,sequence,payload_json,status,attempt,
         max_attempts,lease_owner,lease_until,evidence_json,last_error)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        actions,
    )
    return counts


def _db_count(path: Path, table: str) -> int:
    db = duckdb.connect(str(path), read_only=True)
    try:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--source-archive")
    args = ap.parse_args()

    root = Path(args.dump_dir).resolve()
    data_dir = Path(args.data_dir).resolve()
    if not root.is_dir():
        raise RuntimeError(f"dump directory not found: {root}")

    state = data_dir / "state.duckdb"
    history = data_dir / "history.duckdb"
    orchestration = data_dir / "orchestration.duckdb"
    state_tmp = _fresh(state)
    history_tmp = _fresh(history)
    orchestration_tmp = _fresh(orchestration)

    counts: dict[str, int] = {}
    state_db = _init_state(state_tmp)
    try:
        counts.update(_import_state(root, state_db))
    finally:
        state_db.close()

    history_db = _init_history(history_tmp)
    try:
        counts.update(_import_history(root, history_db))
    finally:
        history_db.close()

    orchestration_db = _init_orchestration(orchestration_tmp)
    try:
        counts.update(_import_orchestration(root, orchestration_db))
    finally:
        orchestration_db.close()

    verification = {
        "forecast_records": _db_count(state_tmp, "forecast_records"),
        "outcome_records": _db_count(state_tmp, "outcome_records"),
        "historical_candles": _db_count(history_tmp, "historical_candles"),
        "historical_verified_ranges": _db_count(history_tmp, "historical_verified_ranges"),
        "historical_sessions": _db_count(history_tmp, "historical_sessions"),
        "historical_session_verified_ranges": _db_count(history_tmp, "historical_session_verified_ranges"),
        "historical_flow_rows": _db_count(history_tmp, "historical_flow_rows"),
        "historical_flow_verified": _db_count(history_tmp, "historical_flow_verified"),
        "orchestration_runs": _db_count(orchestration_tmp, "orchestration_runs"),
        "orchestration_actions": _db_count(orchestration_tmp, "orchestration_actions"),
    }
    for name, expected in counts.items():
        if verification[name] != expected:
            raise RuntimeError(f"verification failed for {name}: {verification[name]} != {expected}")

    _replace(state_tmp, state)
    _replace(history_tmp, history)
    _replace(orchestration_tmp, orchestration)

    manifest = {
        "source_dump_dir": str(root),
        "source_archive_sha256": (
            _sha256(Path(args.source_archive)) if args.source_archive and Path(args.source_archive).is_file() else None
        ),
        "ignored_tables": ["upstream_rate_slots"],
        "reason_ignored": "single-host local runtime uses process-local request pacing; historical reservations are not durable business state",
        "counts": verification,
        "files": {
            "state": {"path": str(state), "bytes": state.stat().st_size, "sha256": _sha256(state)},
            "history": {"path": str(history), "bytes": history.stat().st_size, "sha256": _sha256(history)},
            "orchestration": {
                "path": str(orchestration),
                "bytes": orchestration.stat().st_size,
                "sha256": _sha256(orchestration),
            },
        },
    }
    manifest_path = data_dir / "migration_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("BIRZHA_YDB_TO_DUCKDB=PASS")
    print("COUNTS=" + json.dumps(verification, ensure_ascii=False, sort_keys=True))
    print("MANIFEST=" + str(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
