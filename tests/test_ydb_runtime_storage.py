from __future__ import annotations

from dataclasses import dataclass, field

from birzha.storage.ydb_runtime_storage import (
    YdbRuntimeHistoricalCandleStore,
    YdbRuntimeHistoricalFlowStore,
    YdbRuntimeSlotPacingGate,
)


@dataclass
class FakePool:
    calls: list[str] = field(default_factory=list)

    def execute_with_retries(self, query, parameters=None, **kwargs):
        self.calls.append(str(query))
        return []


def test_runtime_adapters_do_not_issue_schema_ddl_on_construction() -> None:
    pool = FakePool()

    YdbRuntimeSlotPacingGate(pool, provider_key="moex-public")
    YdbRuntimeHistoricalCandleStore(pool)
    YdbRuntimeHistoricalFlowStore(pool)

    assert pool.calls == []


def test_runtime_rate_gate_still_uses_data_path_after_no_ddl_construction() -> None:
    pool = FakePool()
    gate = YdbRuntimeSlotPacingGate(
        pool,
        provider_key="moex-public",
        clock=lambda: 1.0,
        sleeper=lambda _seconds: None,
        safety_margin_seconds=0.0,
    )

    gate.pace(1.0)

    assert len(pool.calls) == 1
    assert "INSERT INTO `upstream_rate_slots`" in pool.calls[0]
    assert "CREATE TABLE" not in pool.calls[0]
