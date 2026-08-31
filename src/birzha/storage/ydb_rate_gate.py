"""Distributed YDB-backed pacing gate for outbound provider requests."""

from __future__ import annotations

import time
from typing import Callable, Protocol

import ydb

from birzha.upstream.safety import UnsafeUpstreamConfiguration


class QueryPool(Protocol):
    def execute_with_retries(self, query: str, parameters: dict[str, object] | None = None, **kwargs: object): ...


class YdbSlotPacingGate:
    """Reserve globally unique time slots in YDB before an upstream attempt.

    Every successful reservation waits until the end of its slot before the
    request may leave the process. Adjacent successful slots are therefore at
    least ``min_interval_seconds`` apart across all server instances.
    """

    scope = "distributed"

    def __init__(
        self,
        pool: QueryPool,
        *,
        provider_key: str,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        table: str = "upstream_rate_slots",
        safety_margin_seconds: float = 0.02,
        max_slot_conflicts: int = 20,
    ) -> None:
        if not provider_key.strip():
            raise ValueError("provider_key must be non-empty")
        self._pool = pool
        self._provider_key = provider_key
        self._clock = clock
        self._sleeper = sleeper
        self._table = table
        self._margin = max(0.0, safety_margin_seconds)
        self._max_conflicts = max_slot_conflicts
        self._init_schema()

    def _init_schema(self) -> None:
        self._pool.execute_with_retries(
            f"""
            CREATE TABLE IF NOT EXISTS `{self._table}` (
                provider_key Utf8 NOT NULL,
                slot_id Uint64 NOT NULL,
                PRIMARY KEY (provider_key, slot_id)
            );
            """,
            retry_settings=ydb.RetrySettings(idempotent=True),
        )

    def pace(self, min_interval_seconds: float) -> None:
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be > 0")

        interval = float(min_interval_seconds)
        for _ in range(self._max_conflicts):
            now = self._clock()
            slot_id = int(now // interval)
            slot_end = (slot_id + 1) * interval
            try:
                self._pool.execute_with_retries(
                    f"""
                    DECLARE $provider_key AS Utf8;
                    DECLARE $slot_id AS Uint64;
                    INSERT INTO `{self._table}` (provider_key, slot_id)
                    VALUES ($provider_key, $slot_id);
                    """,
                    {
                        "$provider_key": (self._provider_key, ydb.PrimitiveType.Utf8),
                        "$slot_id": (slot_id, ydb.PrimitiveType.Uint64),
                    },
                    retry_settings=ydb.RetrySettings(idempotent=False),
                )
            except Exception as exc:
                if not self._slot_exists(slot_id):
                    raise UnsafeUpstreamConfiguration(
                        "distributed upstream pacing could not reserve or verify a YDB slot"
                    ) from exc
                delay = slot_end + self._margin - self._clock()
                if delay > 0:
                    self._sleeper(delay)
                continue

            delay = slot_end + self._margin - self._clock()
            if delay > 0:
                self._sleeper(delay)
            return

        raise UnsafeUpstreamConfiguration("distributed upstream pacing exhausted slot-conflict retries")

    def _slot_exists(self, slot_id: int) -> bool:
        try:
            result = self._pool.execute_with_retries(
                f"""
                DECLARE $provider_key AS Utf8;
                DECLARE $slot_id AS Uint64;
                SELECT slot_id FROM `{self._table}`
                WHERE provider_key = $provider_key AND slot_id = $slot_id;
                """,
                {
                    "$provider_key": (self._provider_key, ydb.PrimitiveType.Utf8),
                    "$slot_id": (slot_id, ydb.PrimitiveType.Uint64),
                },
                retry_settings=ydb.RetrySettings(idempotent=True),
            )
        except Exception as exc:
            raise UnsafeUpstreamConfiguration(
                "distributed upstream pacing verification failed closed"
            ) from exc

        result_sets = list(result) if not isinstance(result, list) else result
        return any(list(getattr(item, "rows", []) or []) for item in result_sets)
