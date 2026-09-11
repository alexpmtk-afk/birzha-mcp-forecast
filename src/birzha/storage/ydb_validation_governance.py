"""Durable one-shot governance for statistical holdout periods."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import ydb


class QueryPool(Protocol):
    def execute_with_retries(
        self,
        query: str,
        parameters: dict[str, object] | None = None,
        **kwargs: object,
    ): ...


class HoldoutAlreadyConsumedError(RuntimeError):
    """Raised when a governed holdout period has already been claimed."""


@dataclass(frozen=True, slots=True)
class HoldoutClaim:
    holdout_start: str
    holdout_end: str
    protocol: str
    model_fingerprint: str
    consumed_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "holdout_start": self.holdout_start,
            "holdout_end": self.holdout_end,
            "protocol": self.protocol,
            "model_fingerprint": self.model_fingerprint,
            "consumed_at": self.consumed_at,
        }


class YdbValidationGovernanceStore:
    """Persistently burns a holdout range before its first evaluation.

    The claim is intentionally written before holdout performance is read. If a
    process crashes after the claim, the range stays consumed. This is stricter
    than retrying and protects the holdout from accidental repeated tuning.
    """

    def __init__(
        self,
        pool: QueryPool,
        *,
        table: str = "model_validation_holdout_claims",
    ) -> None:
        self._pool = pool
        self._table = _safe_table_name(table)
        self._pool.execute_with_retries(
            f"""
            CREATE TABLE IF NOT EXISTS `{self._table}` (
                holdout_start Utf8 NOT NULL,
                holdout_end Utf8 NOT NULL,
                protocol Utf8 NOT NULL,
                model_fingerprint Utf8 NOT NULL,
                consumed_at Utf8 NOT NULL,
                PRIMARY KEY (holdout_start, holdout_end)
            );
            """,
            retry_settings=ydb.RetrySettings(idempotent=True),
        )

    def overlapping_claim(self, holdout_start: str, holdout_end: str) -> HoldoutClaim | None:
        result = self._pool.execute_with_retries(
            f"""
            DECLARE $holdout_start AS Utf8;
            DECLARE $holdout_end AS Utf8;
            SELECT holdout_start, holdout_end, protocol, model_fingerprint, consumed_at
            FROM `{self._table}`
            WHERE holdout_start <= $holdout_end AND holdout_end >= $holdout_start
            LIMIT 1;
            """,
            {
                "$holdout_start": _utf8(holdout_start[:10]),
                "$holdout_end": _utf8(holdout_end[:10]),
            },
            retry_settings=ydb.RetrySettings(idempotent=True),
        )
        row = _first_row(result)
        if row is None:
            return None
        return HoldoutClaim(
            holdout_start=str(_row_value(row, "holdout_start")),
            holdout_end=str(_row_value(row, "holdout_end")),
            protocol=str(_row_value(row, "protocol")),
            model_fingerprint=str(_row_value(row, "model_fingerprint")),
            consumed_at=str(_row_value(row, "consumed_at")),
        )

    def claim_once(
        self,
        *,
        holdout_start: str,
        holdout_end: str,
        protocol: str,
        model_fingerprint: str,
    ) -> HoldoutClaim:
        left = holdout_start[:10]
        right = holdout_end[:10]
        existing = self.overlapping_claim(left, right)
        if existing is not None:
            raise HoldoutAlreadyConsumedError(
                "holdout overlaps a previously consumed range: "
                f"{existing.holdout_start}..{existing.holdout_end}"
            )

        claim = HoldoutClaim(
            holdout_start=left,
            holdout_end=right,
            protocol=protocol,
            model_fingerprint=model_fingerprint,
            consumed_at=datetime.now(timezone.utc).isoformat(),
        )
        try:
            self._pool.execute_with_retries(
                f"""
                DECLARE $holdout_start AS Utf8;
                DECLARE $holdout_end AS Utf8;
                DECLARE $protocol AS Utf8;
                DECLARE $model_fingerprint AS Utf8;
                DECLARE $consumed_at AS Utf8;
                INSERT INTO `{self._table}`
                (holdout_start, holdout_end, protocol, model_fingerprint, consumed_at)
                VALUES ($holdout_start, $holdout_end, $protocol, $model_fingerprint, $consumed_at);
                """,
                {
                    "$holdout_start": _utf8(claim.holdout_start),
                    "$holdout_end": _utf8(claim.holdout_end),
                    "$protocol": _utf8(claim.protocol),
                    "$model_fingerprint": _utf8(claim.model_fingerprint),
                    "$consumed_at": _utf8(claim.consumed_at),
                },
                retry_settings=ydb.RetrySettings(idempotent=True),
            )
        except Exception:
            existing = self.overlapping_claim(left, right)
            if existing is not None:
                raise HoldoutAlreadyConsumedError(
                    "holdout was claimed concurrently or previously: "
                    f"{existing.holdout_start}..{existing.holdout_end}"
                ) from None
            raise
        return claim


def _utf8(value: str):
    return (value, ydb.PrimitiveType.Utf8)


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
