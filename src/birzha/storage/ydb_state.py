"""Durable YDB storage for immutable forecasts and append-only outcomes.

The runtime authenticates with Yandex Cloud metadata credentials. No service
account key is stored in the application. YDB is the production multi-instance
state backend; DuckDB remains the deterministic local/reference backend.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

import ydb
import ydb.iam

from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.domain.outcome import HorizonOutcome
from birzha.storage.forecast_journal import ForecastCollisionError, JournalAppendResult
from birzha.storage.outcome_journal import OutcomeCollisionError


class QueryPool(Protocol):
    def execute_with_retries(self, query: str, parameters: dict[str, object] | None = None, **kwargs: object): ...


@dataclass(slots=True)
class YdbRuntime:
    driver: Any
    pool: QueryPool

    @classmethod
    def connect(cls, connection_string: str, *, wait_timeout: float = 10.0) -> "YdbRuntime":
        if not connection_string.strip():
            raise ValueError("YDB connection string must be non-empty")
        driver = ydb.Driver(
            connection_string=connection_string,
            credentials=ydb.iam.MetadataUrlCredentials(),
        )
        driver.wait(timeout=wait_timeout, fail_fast=True)
        return cls(driver=driver, pool=ydb.QuerySessionPool(driver))

    def close(self) -> None:
        close_pool = getattr(self.pool, "stop", None) or getattr(self.pool, "close", None)
        if callable(close_pool):
            close_pool()
        self.driver.stop(timeout=5)


class YdbForecastJournal:
    storage_scope = "distributed"

    def __init__(self, pool: QueryPool, *, table: str = "forecast_records") -> None:
        self._pool = pool
        self._table = _safe_table_name(table)
        self._init_schema()

    def _init_schema(self) -> None:
        self._pool.execute_with_retries(
            f"""
            CREATE TABLE IF NOT EXISTS `{self._table}` (
                forecast_id Utf8 NOT NULL,
                payload_hash Utf8 NOT NULL,
                payload_json Utf8 NOT NULL,
                symbol Utf8 NOT NULL,
                secid Utf8 NOT NULL,
                created_at_t0 Utf8 NOT NULL,
                engine_version Utf8 NOT NULL,
                PRIMARY KEY (forecast_id)
            );
            """,
            retry_settings=ydb.RetrySettings(idempotent=True),
        )

    @staticmethod
    def canonical_payload(record: ForecastRecord) -> tuple[str, str]:
        payload = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def append(self, record: ForecastRecord) -> JournalAppendResult:
        payload, digest = self.canonical_payload(record)
        existing = self._get_hash(record.forecast_id)
        if existing is not None:
            return self._duplicate_or_collision(record.forecast_id, digest, existing)
        query = f"""
        DECLARE $forecast_id AS Utf8;
        DECLARE $payload_hash AS Utf8;
        DECLARE $payload_json AS Utf8;
        DECLARE $symbol AS Utf8;
        DECLARE $secid AS Utf8;
        DECLARE $created_at_t0 AS Utf8;
        DECLARE $engine_version AS Utf8;
        INSERT INTO `{self._table}`
            (forecast_id, payload_hash, payload_json, symbol, secid, created_at_t0, engine_version)
        VALUES
            ($forecast_id, $payload_hash, $payload_json, $symbol, $secid, $created_at_t0, $engine_version);
        """
        try:
            self._pool.execute_with_retries(
                query,
                {
                    "$forecast_id": _utf8(record.forecast_id),
                    "$payload_hash": _utf8(digest),
                    "$payload_json": _utf8(payload),
                    "$symbol": _utf8(record.symbol),
                    "$secid": _utf8(record.secid),
                    "$created_at_t0": _utf8(record.created_at_t0),
                    "$engine_version": _utf8(record.engine_version),
                },
                retry_settings=ydb.RetrySettings(idempotent=True),
            )
            return JournalAppendResult(record.forecast_id, digest, "APPENDED")
        except Exception:
            winner = self._get_hash(record.forecast_id)
            if winner is None:
                raise
            return self._duplicate_or_collision(record.forecast_id, digest, winner)

    def _duplicate_or_collision(self, forecast_id: str, digest: str, existing: str) -> JournalAppendResult:
        if existing != digest:
            raise ForecastCollisionError(f"forecast_id collision for {forecast_id}: immutable payload differs")
        return JournalAppendResult(forecast_id, digest, "DUPLICATE_IDENTICAL")

    def _get_hash(self, forecast_id: str) -> str | None:
        result = self._pool.execute_with_retries(
            f"""
            DECLARE $forecast_id AS Utf8;
            SELECT payload_hash FROM `{self._table}` WHERE forecast_id = $forecast_id;
            """,
            {"$forecast_id": _utf8(forecast_id)},
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        row = _first_row(result)
        return None if row is None else str(_row_value(row, "payload_hash"))

    def get(self, forecast_id: str) -> ForecastRecord | None:
        result = self._pool.execute_with_retries(
            f"""
            DECLARE $forecast_id AS Utf8;
            SELECT payload_json FROM `{self._table}` WHERE forecast_id = $forecast_id;
            """,
            {"$forecast_id": _utf8(forecast_id)},
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        row = _first_row(result)
        return None if row is None else _forecast_from_dict(json.loads(str(_row_value(row, "payload_json"))))

    def list_recent(self, *, limit: int = 20, symbol: str | None = None) -> list[ForecastRecord]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if symbol:
            query = f"""
            DECLARE $symbol AS Utf8;
            DECLARE $limit AS Uint64;
            SELECT payload_json FROM `{self._table}`
            WHERE symbol = $symbol
            ORDER BY created_at_t0 DESC, forecast_id DESC
            LIMIT $limit;
            """
            params = {"$symbol": _utf8(symbol), "$limit": _uint64(limit)}
        else:
            query = f"""
            DECLARE $limit AS Uint64;
            SELECT payload_json FROM `{self._table}`
            ORDER BY created_at_t0 DESC, forecast_id DESC
            LIMIT $limit;
            """
            params = {"$limit": _uint64(limit)}
        result = self._pool.execute_with_retries(query, params, retry_settings=ydb.RetrySettings(idempotent=True))
        return [_forecast_from_dict(json.loads(str(_row_value(row, "payload_json")))) for row in _rows(result)]

    def count(self) -> int:
        result = self._pool.execute_with_retries(
            f"SELECT COUNT(*) AS cnt FROM `{self._table}`;",
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        row = _first_row(result)
        return int(_row_value(row, "cnt")) if row is not None else 0

    def close(self) -> None:
        return None


class YdbOutcomeJournal:
    storage_scope = "distributed"

    def __init__(self, pool: QueryPool, *, table: str = "outcome_records") -> None:
        self._pool = pool
        self._table = _safe_table_name(table)
        self._init_schema()

    def _init_schema(self) -> None:
        self._pool.execute_with_retries(
            f"""
            CREATE TABLE IF NOT EXISTS `{self._table}` (
                outcome_id Utf8 NOT NULL,
                forecast_id Utf8 NOT NULL,
                horizon_sessions Int32 NOT NULL,
                payload_hash Utf8 NOT NULL,
                payload_json Utf8 NOT NULL,
                PRIMARY KEY (outcome_id)
            );
            """,
            retry_settings=ydb.RetrySettings(idempotent=True),
        )

    @staticmethod
    def canonical_payload(record: HorizonOutcome) -> tuple[str, str]:
        payload = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def append(self, record: HorizonOutcome) -> str:
        payload, digest = self.canonical_payload(record)
        existing = self._get_hash(record.outcome_id)
        if existing is not None:
            return self._duplicate_or_collision(record.outcome_id, digest, existing)
        try:
            self._pool.execute_with_retries(
                f"""
                DECLARE $outcome_id AS Utf8;
                DECLARE $forecast_id AS Utf8;
                DECLARE $horizon_sessions AS Int32;
                DECLARE $payload_hash AS Utf8;
                DECLARE $payload_json AS Utf8;
                INSERT INTO `{self._table}`
                    (outcome_id, forecast_id, horizon_sessions, payload_hash, payload_json)
                VALUES
                    ($outcome_id, $forecast_id, $horizon_sessions, $payload_hash, $payload_json);
                """,
                {
                    "$outcome_id": _utf8(record.outcome_id),
                    "$forecast_id": _utf8(record.forecast_id),
                    "$horizon_sessions": _int32(record.horizon_sessions),
                    "$payload_hash": _utf8(digest),
                    "$payload_json": _utf8(payload),
                },
                retry_settings=ydb.RetrySettings(idempotent=True),
            )
            return "APPENDED"
        except Exception:
            winner = self._get_hash(record.outcome_id)
            if winner is None:
                raise
            return self._duplicate_or_collision(record.outcome_id, digest, winner)

    def _duplicate_or_collision(self, outcome_id: str, digest: str, existing: str) -> str:
        if existing != digest:
            raise OutcomeCollisionError(f"outcome collision for {outcome_id}")
        return "DUPLICATE_IDENTICAL"

    def _get_hash(self, outcome_id: str) -> str | None:
        result = self._pool.execute_with_retries(
            f"""
            DECLARE $outcome_id AS Utf8;
            SELECT payload_hash FROM `{self._table}` WHERE outcome_id = $outcome_id;
            """,
            {"$outcome_id": _utf8(outcome_id)},
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        row = _first_row(result)
        return None if row is None else str(_row_value(row, "payload_hash"))

    def list_for_forecast(self, forecast_id: str) -> list[HorizonOutcome]:
        result = self._pool.execute_with_retries(
            f"""
            DECLARE $forecast_id AS Utf8;
            SELECT payload_json FROM `{self._table}`
            WHERE forecast_id = $forecast_id
            ORDER BY horizon_sessions;
            """,
            {"$forecast_id": _utf8(forecast_id)},
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        return [_outcome_from_dict(json.loads(str(_row_value(row, "payload_json")))) for row in _rows(result)]

    def close(self) -> None:
        return None


def _utf8(value: str):
    return (value, ydb.PrimitiveType.Utf8)


def _uint64(value: int):
    return (value, ydb.PrimitiveType.Uint64)


def _int32(value: int):
    return (value, ydb.PrimitiveType.Int32)


def _rows(result: object) -> list[object]:
    if result is None:
        return []
    result_sets = list(result) if not isinstance(result, list) else result
    rows: list[object] = []
    for result_set in result_sets:
        rows.extend(list(getattr(result_set, "rows", []) or []))
    return rows


def _first_row(result: object) -> object | None:
    rows = _rows(result)
    return rows[0] if rows else None


def _row_value(row: object, field: str) -> object:
    if isinstance(row, dict):
        return row[field]
    try:
        return row[field]  # type: ignore[index]
    except (TypeError, KeyError):
        return getattr(row, field)


def _safe_table_name(value: str) -> str:
    if not value or any(not (char.isalnum() or char == "_") for char in value):
        raise ValueError("YDB table name must contain only letters, digits and underscore")
    return value


def _forecast_from_dict(payload: dict[str, object]) -> ForecastRecord:
    horizons_raw = payload.get("horizons") or []
    horizons = tuple(
        HorizonForecast(
            sessions=int(item["sessions"]),
            direction=str(item["direction"]),
            signal_strength=float(item["signal_strength"]),
            expected_move_pct=float(item["expected_move_pct"]) if item.get("expected_move_pct") is not None else None,
            adverse_move_pct=float(item["adverse_move_pct"]) if item.get("adverse_move_pct") is not None else None,
        )
        for item in horizons_raw
    )
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


def _outcome_from_dict(data: dict[str, object]) -> HorizonOutcome:
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
