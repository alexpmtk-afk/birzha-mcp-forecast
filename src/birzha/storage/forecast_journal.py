"""Immutable Forecast Journal with deterministic hashes and collision checks.

DuckDB is the first local/reference backend. The persistence contract is kept
separate from cloud durability so it can later be moved to a transactional
remote backend without changing forecast identity semantics.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb

from birzha.domain.forecast import ForecastRecord, HorizonForecast


class ForecastCollisionError(RuntimeError):
    """Same forecast identity was presented with different immutable content."""


@dataclass(frozen=True, slots=True)
class JournalAppendResult:
    forecast_id: str
    payload_hash: str
    status: str

    def to_dict(self) -> dict[str, str]:
        return {"forecast_id": self.forecast_id, "payload_hash": self.payload_hash, "status": self.status}


class DuckDBForecastJournal:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(path)
        self._lock = threading.RLock()
        self._init_schema()

    @property
    def storage_scope(self) -> str:
        return "memory" if self.path == ":memory:" else "local_file"

    def _init_schema(self) -> None:
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS forecast_records (
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

    @staticmethod
    def canonical_payload(record: ForecastRecord) -> tuple[str, str]:
        payload = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def append(self, record: ForecastRecord) -> JournalAppendResult:
        payload, digest = self.canonical_payload(record)
        with self._lock:
            self._connection.execute("BEGIN TRANSACTION")
            try:
                existing = self._connection.execute("SELECT payload_hash FROM forecast_records WHERE forecast_id = ?", [record.forecast_id]).fetchone()
                if existing is not None:
                    if str(existing[0]) != digest:
                        raise ForecastCollisionError(f"forecast_id collision for {record.forecast_id}: immutable payload differs")
                    self._connection.execute("COMMIT")
                    return JournalAppendResult(record.forecast_id, digest, "DUPLICATE_IDENTICAL")
                self._connection.execute("""
                    INSERT INTO forecast_records (forecast_id, payload_hash, payload_json, symbol, secid, created_at_t0, engine_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [record.forecast_id, digest, payload, record.symbol, record.secid, record.created_at_t0, record.engine_version])
                self._connection.execute("COMMIT")
                return JournalAppendResult(record.forecast_id, digest, "APPENDED")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get(self, forecast_id: str) -> ForecastRecord | None:
        with self._lock:
            row = self._connection.execute("SELECT payload_json FROM forecast_records WHERE forecast_id = ?", [forecast_id]).fetchone()
        return None if row is None else _record_from_dict(json.loads(str(row[0])))

    def list_recent(self, *, limit: int = 20, symbol: str | None = None) -> list[ForecastRecord]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if symbol:
            query = "SELECT payload_json FROM forecast_records WHERE upper(symbol) = upper(?) ORDER BY inserted_at DESC, forecast_id DESC LIMIT ?"
            args = [symbol, limit]
        else:
            query = "SELECT payload_json FROM forecast_records ORDER BY inserted_at DESC, forecast_id DESC LIMIT ?"
            args = [limit]
        with self._lock:
            rows = self._connection.execute(query, args).fetchall()
        return [_record_from_dict(json.loads(str(row[0]))) for row in rows]

    def count(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT count(*) FROM forecast_records").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _record_from_dict(payload: dict[str, object]) -> ForecastRecord:
    horizons_raw = payload.get("horizons") or []
    horizons = tuple(HorizonForecast(
        sessions=int(item["sessions"]), direction=str(item["direction"]), signal_strength=float(item["signal_strength"]),
        expected_move_pct=float(item["expected_move_pct"]) if item.get("expected_move_pct") is not None else None,
        adverse_move_pct=float(item["adverse_move_pct"]) if item.get("adverse_move_pct") is not None else None,
    ) for item in horizons_raw)
    reference_raw = payload.get("reference_price")
    return ForecastRecord(
        forecast_id=str(payload["forecast_id"]), symbol=str(payload["symbol"]), secid=str(payload["secid"]),
        created_at_t0=str(payload["created_at_t0"]), engine_version=str(payload["engine_version"]),
        direction=str(payload["direction"]), signal_strength=float(payload["signal_strength"]),
        control=str(payload["control"]), route=str(payload["route"]), horizons=horizons,
        reasons=tuple(str(item) for item in (payload.get("reasons") or [])),
        warnings=tuple(str(item) for item in (payload.get("warnings") or [])),
        validation_status=str(payload["validation_status"]),
        reference_price=float(reference_raw) if reference_raw is not None else None,
    )
