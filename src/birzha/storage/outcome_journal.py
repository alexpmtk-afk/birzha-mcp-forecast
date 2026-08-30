"""Append-only Outcome Journal backed by DuckDB reference storage."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import duckdb

from birzha.domain.outcome import HorizonOutcome


class OutcomeCollisionError(RuntimeError):
    pass


class DuckDBOutcomeJournal:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(path)
        self._lock = threading.RLock()
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS outcome_records (
                outcome_id VARCHAR PRIMARY KEY,
                forecast_id VARCHAR NOT NULL,
                horizon_sessions INTEGER NOT NULL,
                payload_hash VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                inserted_at TIMESTAMP DEFAULT current_timestamp
            )
        """)

    @staticmethod
    def canonical_payload(record: HorizonOutcome) -> tuple[str, str]:
        payload = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def append(self, record: HorizonOutcome) -> str:
        payload, digest = self.canonical_payload(record)
        with self._lock:
            existing = self._connection.execute("SELECT payload_hash FROM outcome_records WHERE outcome_id = ?", [record.outcome_id]).fetchone()
            if existing is not None:
                if str(existing[0]) != digest:
                    raise OutcomeCollisionError(f"outcome collision for {record.outcome_id}")
                return "DUPLICATE_IDENTICAL"
            self._connection.execute(
                "INSERT INTO outcome_records (outcome_id, forecast_id, horizon_sessions, payload_hash, payload_json) VALUES (?, ?, ?, ?, ?)",
                [record.outcome_id, record.forecast_id, record.horizon_sessions, digest, payload],
            )
        return "APPENDED"

    def list_for_forecast(self, forecast_id: str) -> list[HorizonOutcome]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM outcome_records WHERE forecast_id = ? ORDER BY horizon_sessions",
                [forecast_id],
            ).fetchall()
        return [_from_dict(json.loads(str(row[0]))) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _from_dict(data: dict[str, object]) -> HorizonOutcome:
    return HorizonOutcome(
        outcome_id=str(data["outcome_id"]), forecast_id=str(data["forecast_id"]), symbol=str(data["symbol"]), secid=str(data["secid"]),
        horizon_sessions=int(data["horizon_sessions"]), reference_price=float(data["reference_price"]),
        target_session_end=str(data["target_session_end"]), target_close=float(data["target_close"]),
        actual_return_pct=float(data["actual_return_pct"]),
        direction_hit=bool(data["direction_hit"]) if data.get("direction_hit") is not None else None,
        max_favorable_excursion_pct=float(data["max_favorable_excursion_pct"]) if data.get("max_favorable_excursion_pct") is not None else None,
        max_adverse_excursion_pct=float(data["max_adverse_excursion_pct"]) if data.get("max_adverse_excursion_pct") is not None else None,
        status=str(data.get("status") or "OBSERVED"),
    )
