"""Persistent raw analytical rows for TradeStats and FUTOI."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Protocol

import duckdb


class HistoricalFlowStore(Protocol):
    def upsert_rows(self, dataset: str, key: str, rows: list[dict[str, object]], source: str) -> int: ...
    def read_rows(self, dataset: str, key: str, from_date: str, till_date: str) -> list[dict[str, object]]: ...
    def is_verified(self, dataset: str, key: str, from_date: str, till_date: str) -> bool: ...
    def mark_verified(self, dataset: str, key: str, from_date: str, till_date: str) -> None: ...


class DuckDBHistoricalFlowStore:
    storage_scope = "local"

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(path)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_flow_rows (
                dataset VARCHAR NOT NULL,
                key_symbol VARCHAR NOT NULL,
                row_key VARCHAR NOT NULL,
                trade_date VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY (dataset, key_symbol, row_key)
            )
        """)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_flow_verified (
                dataset VARCHAR NOT NULL,
                key_symbol VARCHAR NOT NULL,
                from_date VARCHAR NOT NULL,
                till_date VARCHAR NOT NULL,
                PRIMARY KEY (dataset, key_symbol, from_date, till_date)
            )
        """)

    def upsert_rows(self, dataset: str, key: str, rows: list[dict[str, object]], source: str) -> int:
        materialized=[]
        for row in rows:
            payload=json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            materialized.append([dataset,key,_row_key(row,payload),_trade_date(row),payload,source])
        if not materialized:
            return 0
        with self._lock:
            self._connection.executemany("""
                INSERT OR REPLACE INTO historical_flow_rows
                (dataset,key_symbol,row_key,trade_date,payload_json,source)
                VALUES (?,?,?,?,?,?)
            """, materialized)
        return len(materialized)

    def read_rows(self, dataset: str, key: str, from_date: str, till_date: str) -> list[dict[str, object]]:
        with self._lock:
            rows=self._connection.execute("""
                SELECT payload_json FROM historical_flow_rows
                WHERE dataset=? AND key_symbol=? AND trade_date>=? AND trade_date<=?
                ORDER BY trade_date,row_key
            """,[dataset,key,from_date,till_date]).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def is_verified(self, dataset: str, key: str, from_date: str, till_date: str) -> bool:
        with self._lock:
            row=self._connection.execute("""
                SELECT 1 FROM historical_flow_verified
                WHERE dataset=? AND key_symbol=? AND from_date<=? AND till_date>=? LIMIT 1
            """,[dataset,key,from_date,till_date]).fetchone()
        return row is not None

    def mark_verified(self, dataset: str, key: str, from_date: str, till_date: str) -> None:
        with self._lock:
            self._connection.execute("INSERT OR IGNORE INTO historical_flow_verified VALUES (?,?,?,?)",[dataset,key,from_date,till_date])

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _first(row: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _trade_date(row: dict[str, object]) -> str:
    raw=_first(row,"tradedate","TRADEDATE")
    if raw is not None:
        return str(raw)[:10]
    raw=_first(row,"systime","SYSTIME","timestamp","TIMESTAMP")
    return str(raw)[:10] if raw is not None else ""


def _row_key(row: dict[str, object], payload: str) -> str:
    parts=[
        str(_first(row,"tradedate","TRADEDATE") or ""),
        str(_first(row,"tradetime","TRADETIME","systime","SYSTIME") or ""),
        str(_first(row,"seqnum","SEQNUM") or ""),
        str(_first(row,"clgroup","CLGROUP") or ""),
    ]
    identity="|".join(parts)
    if identity == "|||":
        identity=payload
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
