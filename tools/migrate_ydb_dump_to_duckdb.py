#!/usr/bin/env python3
"""Migrate a YDB tools-dump archive into BIRZHA local DuckDB state.

The YDB dump CSV files have no header row. Utf8 values are URL-encoded by the
YDB dump format and are decoded with urllib.parse.unquote_plus.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import unquote_plus

import duckdb

from birzha.storage.duckdb_orchestration_store import DuckDBOrchestrationStore
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore
from birzha.storage.historical_store import DuckDBHistoricalCandleStore
from birzha.storage.outcome_journal import DuckDBOutcomeJournal


def text(value: str) -> str:
    return unquote_plus(value)


def read_table(root: Path, name: str, expected_columns: int) -> list[list[str]]:
    path = root / name / "data_00.csv"
    if not path.is_file():
        return []
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row_no, row in enumerate(csv.reader(handle), 1):
            if len(row) != expected_columns:
                raise RuntimeError(
                    f"{name}: row {row_no} has {len(row)} columns, expected {expected_columns}"
                )
            rows.append(row)
    return rows


def _csv_value(value: object) -> object:
    return r"\N" if value is None else value


def build_candle_stage(root: Path, stage_path: Path) -> int:
    source = root / "historical_candles" / "data_00.csv"
    if not source.is_file():
        return 0
    count = 0
    with source.open("r", encoding="utf-8", newline="") as src, stage_path.open(
        "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.reader(src)
        writer = csv.writer(dst, lineterminator="\n")
        for row_no, row in enumerate(reader, 1):
            if len(row) != 6:
                raise RuntimeError(
                    f"historical_candles: row {row_no} has {len(row)} columns, expected 6"
                )
            secid = text(row[0])
            timeframe = text(row[1])
            begin = text(row[2])
            end_time = text(row[3])
            payload = json.loads(text(row[4]))
            source_name = text(row[5])
            instrument = payload.get("instrument") or {}
            candle = payload.get("candle") or {}
            writer.writerow(
                [
                    secid,
                    str(instrument.get("symbol") or ""),
                    _csv_value(instrument.get("root_symbol")),
                    str(instrument.get("board") or ""),
                    str(instrument.get("engine") or ""),
                    str(instrument.get("market") or ""),
                    str(instrument.get("asset_class") or "unknown"),
                    timeframe,
                    begin,
                    end_time,
                    _csv_value(candle.get("open")),
                    _csv_value(candle.get("close")),
                    _csv_value(candle.get("high")),
                    _csv_value(candle.get("low")),
                    _csv_value(candle.get("value")),
                    _csv_value(candle.get("volume")),
                    "true" if bool(candle.get("completed")) else "false",
                    source_name,
                ]
            )
            count += 1
            if count % 25000 == 0:
                print(f"CANDLE_STAGE_ROWS={count}", flush=True)
    print(f"CANDLE_STAGE_ROWS={count}", flush=True)
    return count


def _duckdb_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def safe_extract(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tf:
        base = destination.resolve()
        for member in tf.getmembers():
            target = (destination / member.name).resolve()
            if target != base and base not in target.parents:
                raise RuntimeError(f"unsafe archive member: {member.name}")
        tf.extractall(destination)
    roots = [p for p in destination.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"expected exactly one dump root, got {len(roots)}")
    return roots[0]


def init_databases(state_db: Path, history_db: Path) -> None:
    state_db.parent.mkdir(parents=True, exist_ok=True)
    history_db.parent.mkdir(parents=True, exist_ok=True)

    x = DuckDBForecastJournal(str(state_db))
    x.close()
    x = DuckDBOutcomeJournal(str(state_db))
    x.close()
    x = DuckDBOrchestrationStore(str(state_db))
    x.close()

    x = DuckDBHistoricalCandleStore(str(history_db))
    x.close()
    x = DuckDBHistoricalFlowStore(str(history_db))
    x.close()


def migrate_state(root: Path, state_db: Path) -> dict[str, int]:
    forecasts = read_table(root, "forecast_records", 7)
    outcomes = read_table(root, "outcome_records", 5)
    runs = read_table(root, "orchestration_runs", 8)
    actions = read_table(root, "orchestration_actions", 13)
    slots = read_table(root, "upstream_rate_slots", 2)

    con = duckdb.connect(str(state_db))
    try:
        con.execute("BEGIN TRANSACTION")
        con.executemany(
            """
            INSERT OR REPLACE INTO forecast_records
            (forecast_id,payload_hash,payload_json,symbol,secid,created_at_t0,engine_version)
            VALUES (?,?,?,?,?,?,?)
            """,
            [[text(v) for v in row] for row in forecasts],
        )
        con.executemany(
            """
            INSERT OR REPLACE INTO outcome_records
            (outcome_id,forecast_id,horizon_sessions,payload_hash,payload_json)
            VALUES (?,?,?,?,?)
            """,
            [
                [text(r[0]), text(r[1]), int(r[2]), text(r[3]), text(r[4])]
                for r in outcomes
            ],
        )
        con.executemany(
            """
            INSERT OR REPLACE INTO orchestration_runs
            (workflow_id,kind,stage,status,created_at,updated_at,metadata_json,last_error)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            [[text(v) for v in row] for row in runs],
        )
        con.executemany(
            """
            INSERT OR REPLACE INTO orchestration_actions
            (action_id,workflow_id,stage,kind,sequence,payload_json,status,attempt,
             max_attempts,lease_owner,lease_until,evidence_json,last_error)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                [
                    text(r[0]), text(r[1]), text(r[2]), text(r[3]), int(r[4]),
                    text(r[5]), text(r[6]), int(r[7]), int(r[8]), text(r[9]),
                    text(r[10]), text(r[11]), text(r[12]),
                ]
                for r in actions
            ],
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_upstream_rate_slots (
                provider_key VARCHAR NOT NULL,
                slot BIGINT NOT NULL,
                PRIMARY KEY(provider_key, slot)
            )
            """
        )
        con.executemany(
            "INSERT OR IGNORE INTO migration_upstream_rate_slots VALUES (?,?)",
            [[text(r[0]), int(r[1])] for r in slots],
        )
        con.execute("COMMIT")

        expected = {
            "forecast_records": len(forecasts),
            "outcome_records": len(outcomes),
            "orchestration_runs": len(runs),
            "orchestration_actions": len(actions),
            "migration_upstream_rate_slots": len(slots),
        }
        for table, count in expected.items():
            actual = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            if actual != count:
                raise RuntimeError(f"{table}: imported {actual}, expected {count}")
        return expected
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def migrate_history(root: Path, history_db: Path) -> dict[str, int]:
    verified = read_table(root, "historical_candles_verified_ranges", 4)
    sessions = read_table(root, "historical_candles_sessions", 3)
    session_verified = read_table(root, "historical_candles_session_verified_ranges", 3)
    flow = read_table(root, "historical_flow_rows", 6)
    flow_verified = read_table(root, "historical_flow_rows_verified", 4)

    candle_stage = history_db.parent / (history_db.name + ".candles.csv")
    candle_count = build_candle_stage(root, candle_stage)

    con = duckdb.connect(str(history_db))
    try:
        con.execute("BEGIN TRANSACTION")
        if candle_count:
            stage = _duckdb_path(candle_stage)
            con.execute(
                f"""
                COPY historical_candles
                (secid,symbol,root_symbol,board,engine,market,asset_class,timeframe,
                 begin,end_time,open,close,high,low,value,volume,completed,source)
                FROM '{stage}'
                (FORMAT CSV, HEADER FALSE, NULL '\\N')
                """
            )
        con.executemany(
            "INSERT OR IGNORE INTO historical_verified_ranges VALUES (?,?,?,?)",
            [[text(v) for v in row] for row in verified],
        )
        con.executemany(
            "INSERT OR IGNORE INTO historical_sessions VALUES (?,?,?)",
            [[text(v) for v in row] for row in sessions],
        )
        con.executemany(
            "INSERT OR IGNORE INTO historical_session_verified_ranges VALUES (?,?,?)",
            [[text(v) for v in row] for row in session_verified],
        )
        con.executemany(
            """
            INSERT OR REPLACE INTO historical_flow_rows
            (dataset,key_symbol,row_key,trade_date,payload_json,source)
            VALUES (?,?,?,?,?,?)
            """,
            [[text(v) for v in row] for row in flow],
        )
        con.executemany(
            "INSERT OR IGNORE INTO historical_flow_verified VALUES (?,?,?,?)",
            [[text(v) for v in row] for row in flow_verified],
        )
        con.execute("COMMIT")

        expected = {
            "historical_candles": candle_count,
            "historical_verified_ranges": len(verified),
            "historical_sessions": len(sessions),
            "historical_session_verified_ranges": len(session_verified),
            "historical_flow_rows": len(flow),
            "historical_flow_verified": len(flow_verified),
        }
        for table, count in expected.items():
            actual = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            if actual != count:
                raise RuntimeError(f"{table}: imported {actual}, expected {count}")
        return expected
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()
        candle_stage.unlink(missing_ok=True)



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", required=True, type=Path)
    parser.add_argument("--state-db", required=True, type=Path)
    parser.add_argument("--history-db", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not args.dump.is_file():
        raise SystemExit("dump archive does not exist")

    for path in (args.state_db, args.history_db):
        if path.exists():
            if not args.overwrite:
                raise SystemExit(f"destination exists; use --overwrite: {path}")
            path.unlink()

    temp = Path(tempfile.mkdtemp(prefix="birzha-ydb-migrate-"))
    try:
        print("PHASE=extract", flush=True)
        root = safe_extract(args.dump, temp)
        print("PHASE=init_databases", flush=True)
        init_databases(args.state_db, args.history_db)
        print("PHASE=migrate_state", flush=True)
        state = migrate_state(root, args.state_db)
        print("STATE_MIGRATION_COUNTS=" + json.dumps(state, sort_keys=True), flush=True)
        print("PHASE=migrate_history", flush=True)
        history = migrate_history(root, args.history_db)
        print("HISTORY_MIGRATION_COUNTS=" + json.dumps(history, sort_keys=True), flush=True)
        print("BIRZHA_YDB_TO_DUCKDB=PASS")
        print("STATE_COUNTS=" + json.dumps(state, sort_keys=True))
        print("HISTORY_COUNTS=" + json.dumps(history, sort_keys=True))
        print("STATE_DB_BYTES=" + str(args.state_db.stat().st_size))
        print("HISTORY_DB_BYTES=" + str(args.history_db.stat().st_size))
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
