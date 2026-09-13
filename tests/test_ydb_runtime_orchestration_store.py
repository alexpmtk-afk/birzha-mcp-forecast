from __future__ import annotations

from pathlib import Path

from birzha.storage.ydb_orchestration_store import YdbOrchestrationStore
from birzha.storage.ydb_runtime_orchestration_store import YdbRuntimeOrchestrationStore


class FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None, object]] = []

    def execute_with_retries(self, query, parameters=None, **kwargs):
        self.calls.append((query, parameters, kwargs.get("retry_settings")))
        return []


def test_bootstrap_store_still_provisions_both_schema_tables() -> None:
    pool = FakePool()
    YdbOrchestrationStore(pool)  # type: ignore[arg-type]

    assert len(pool.calls) == 2
    assert all("CREATE TABLE IF NOT EXISTS" in query for query, _, _ in pool.calls)
    assert "orchestration_runs" in pool.calls[0][0]
    assert "orchestration_actions" in pool.calls[1][0]


def test_runtime_store_cold_start_never_issues_schema_ddl() -> None:
    pool = FakePool()
    store = YdbRuntimeOrchestrationStore(pool)  # type: ignore[arg-type]

    assert pool.calls == []
    assert store.list_workflows(active_only=True, limit=25) == []
    assert len(pool.calls) == 1
    query = pool.calls[0][0]
    assert "CREATE TABLE" not in query
    assert "ALTER TABLE" not in query
    assert "DROP TABLE" not in query
    assert "SELECT * FROM `orchestration_runs`" in query


def test_production_runtime_entrypoints_use_no_ddl_store() -> None:
    root = Path(__file__).resolve().parents[1]
    worker = (root / "src" / "birzha" / "worker.py").read_text(encoding="utf-8")
    extension = (
        root / "src" / "birzha" / "mcp" / "orchestration_extension.py"
    ).read_text(encoding="utf-8")

    assert "YdbRuntimeOrchestrationStore(_runtime.pool)" in worker
    assert "YdbRuntimeOrchestrationStore(ydb_runtime.pool)" in extension
    assert "WorkflowOrchestrator(YdbOrchestrationStore(_runtime.pool))" not in worker
    assert "store = YdbOrchestrationStore(ydb_runtime.pool)" not in extension
