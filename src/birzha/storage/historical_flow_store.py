"""Persistent raw analytical rows for TradeStats and FUTOI."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import duckdb


class HistoricalFlowStore(Protocol):
    def upsert_rows(self, dataset: str, key: str, rows: list[dict[str, object]], source: str) -> int: ...
    def read_rows(self, dataset: str, key: str, from_date: str, till_date: str) -> list[dict[str, object]]: ...
    def read_rows_causal(self, dataset: str, key: str, from_date: str, till_date: str, cutoff_at: str) -> list[dict[str, object]]: ...
    def read_rows_causal(
        self,
        dataset: str,
        key: str,
        from_date: str,
        till_date: str,
        cutoff_at: str,
    ) -> list[dict[str, object]]:
        cutoff=_parse_time(cutoff_at)
        if cutoff is None:
            raise ValueError("cutoff_at must be a parseable timestamp")
        with self._lock:
            rows=self._connection.execute("""
                SELECT payload_json, available_at
                FROM historical_flow_rows
                WHERE dataset=? AND key_symbol=? AND trade_date>=? AND trade_date<=?
                ORDER BY trade_date,row_key
            """,[dataset,key,from_date,till_date]).fetchall()
        result=[]
        for payload_json, available_at in rows:
            available=_parse_time(str(available_at)) if available_at is not None else None
            if available is None or available > cutoff:
                continue
            result.append(json.loads(str(payload_json)))
        return result

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
                available_at VARCHAR,
                available_at_confidence VARCHAR NOT NULL DEFAULT 'UNKNOWN',
                observed_at VARCHAR,
                revision VARCHAR,
                PRIMARY KEY (dataset, key_symbol, row_key)
            )
        """)
        self._connection.execute("ALTER TABLE historical_flow_rows ADD COLUMN IF NOT EXISTS available_at VARCHAR")
        self._connection.execute("ALTER TABLE historical_flow_rows ADD COLUMN IF NOT EXISTS available_at_confidence VARCHAR DEFAULT 'UNKNOWN'")
        self._connection.execute("ALTER TABLE historical_flow_rows ADD COLUMN IF NOT EXISTS observed_at VARCHAR")
        self._connection.execute("ALTER TABLE historical_flow_rows ADD COLUMN IF NOT EXISTS revision VARCHAR")
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
        observed_at=datetime.now(timezone.utc).isoformat()
        for row in rows:
            payload=json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            available_at, confidence=_availability(dataset, row, source)
            revision=hashlib.sha256(payload.encode("utf-8")).hexdigest()
            materialized.append([
                dataset,key,_row_key(row,payload),_trade_date(row),payload,source,
                available_at,confidence,observed_at,revision,
            ])
        if not materialized:
            return 0
        with self._lock:
            self._connection.executemany("""
                INSERT INTO historical_flow_rows
                (dataset,key_symbol,row_key,trade_date,payload_json,source,
                 available_at,available_at_confidence,observed_at,revision)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT (dataset,key_symbol,row_key) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    source=excluded.source,
                    available_at=COALESCE(historical_flow_rows.available_at, excluded.available_at),
                    available_at_confidence=CASE
                        WHEN historical_flow_rows.available_at IS NOT NULL
                        THEN historical_flow_rows.available_at_confidence
                        ELSE excluded.available_at_confidence
                    END,
                    observed_at=COALESCE(historical_flow_rows.observed_at, excluded.observed_at),
                    revision=excluded.revision
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
        str(_first(row,"recno","RECNO") or ""),
        str(_first(row,"tradeno","TRADENO") or ""),
        str(_first(row,"seqnum","SEQNUM") or ""),
        str(_first(row,"clgroup","CLGROUP") or ""),
    ]
    identity="|".join(parts)
    if identity == "|||||":
        identity=payload
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()



MOEX_TIMEZONE = ZoneInfo("Europe/Moscow")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw=value.strip().replace("Z","+00:00")
    try:
        parsed=datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MOEX_TIMEZONE)
    return parsed.astimezone(MOEX_TIMEZONE)


def _event_time(row: dict[str, object]) -> str | None:
    tradedate=str(_first(row,"tradedate","TRADEDATE") or "").strip()
    tradetime=str(_first(row,"tradetime","TRADETIME") or "").strip()
    if tradedate and tradetime:
        return f"{tradedate}T{tradetime}+03:00"
    return None


def _availability(
    dataset: str,
    row: dict[str, object],
    source: str,
) -> tuple[str | None, str]:
    explicit=_first(
        row,
        "available_at","AVAILABLE_AT",
        "systime","SYSTIME",
        "timestamp","TIMESTAMP",
    )
    if explicit is not None and _parse_time(str(explicit)) is not None:
        return str(explicit), "DOCUMENTED"

    normalized=dataset.upper()
    if normalized in {"TRADESTATS","PUBLIC_TRADES_RAW"}:
        event=_event_time(row)
        if event is not None:
            return event, "INFERRED"

    # FUTOI can be delayed for non-subscriber/public access. Its market-event
    # timestamp is not proof that the row was available to BIRZHA at that time.
    return None, "UNKNOWN"
