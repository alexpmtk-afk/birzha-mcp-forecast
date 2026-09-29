from __future__ import annotations

from pathlib import Path

from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.config import Settings
from birzha.domain.orchestration import ActionStatus, WorkflowStage
from birzha.mcp.orchestration_extension import install_orchestration_tools
from birzha.storage.duckdb_orchestration_store import DuckDBOrchestrationStore


class _DummyMcp:
    def tool(self, **_: object):
        def decorate(function):
            return function

        return decorate


def _open(path: Path) -> DuckDBOrchestrationStore:
    return DuckDBOrchestrationStore(str(path))


def test_duckdb_orchestration_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "birzha_state.duckdb"
    first = _open(path)
    service = WorkflowOrchestrator(first)
    run, created = service.start_core_validation(
        development_start="2021-01-01",
        split_date="2022-12-31",
        holdout_end="2024-12-31",
        source_sha="a" * 40,
    )
    assert created is True

    claimed = first.claim_next(
        run.workflow_id,
        stage=WorkflowStage.HISTORY_PREPARATION,
        worker_id="worker-before-restart",
        now="2026-09-29T10:00:00+00:00",
        lease_until="2026-09-29T10:15:00+00:00",
    )
    assert claimed is not None
    assert claimed.status == ActionStatus.RUNNING
    assert claimed.lease_owner == "worker-before-restart"
    first.close()

    reopened = _open(path)
    try:
        persisted = reopened.get_workflow(run.workflow_id)
        assert persisted is not None
        assert persisted.workflow_id == run.workflow_id

        workflows = reopened.list_workflows(active_only=False, limit=100)
        assert [item.workflow_id for item in workflows] == [run.workflow_id]

        actions = reopened.list_actions(run.workflow_id)
        assert actions
        restored = next(item for item in actions if item.action_id == claimed.action_id)
        assert restored.status == ActionStatus.RUNNING
        assert restored.lease_owner == "worker-before-restart"

        finished = reopened.finish_action(
            claimed.action_id,
            worker_id="worker-before-restart",
            status=ActionStatus.PASS,
            evidence={"proof": "persisted"},
        )
        assert finished.status == ActionStatus.PASS
        assert finished.evidence == {"proof": "persisted"}
    finally:
        reopened.close()


def test_duckdb_store_reads_preexisting_migrated_tables(tmp_path: Path) -> None:
    path = tmp_path / "migrated.duckdb"
    store = _open(path)
    try:
        store._connection.execute(
            """
            INSERT INTO orchestration_runs
            (workflow_id, kind, stage, status, created_at, updated_at,
             metadata_json, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "migrated-1",
                "CORE_VALIDATION_V1",
                "HISTORY_PREPARATION",
                "RUNNING",
                "2026-09-18T00:00:00+00:00",
                "2026-09-18T00:00:00+00:00",
                '{"source_sha":"legacy"}',
                "",
            ],
        )
        store._connection.execute(
            """
            INSERT INTO orchestration_actions
            (action_id, workflow_id, stage, kind, sequence, payload_json,
             status, attempt, max_attempts, lease_owner, lease_until,
             evidence_json, last_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "migrated-action-1",
                "migrated-1",
                "HISTORY_PREPARATION",
                "HISTORY_SYNC_CHUNK",
                1,
                '{"symbol":"BR"}',
                "PASS",
                1,
                3,
                "",
                "",
                '{"proof":"old-ydb-dump"}',
                "",
            ],
        )

        run = store.get_workflow("migrated-1")
        assert run is not None
        assert run.metadata["source_sha"] == "legacy"

        actions = store.list_actions("migrated-1")
        assert len(actions) == 1
        assert actions[0].payload["symbol"] == "BR"
        assert actions[0].evidence["proof"] == "old-ydb-dump"
    finally:
        store.close()


def test_install_orchestration_tools_uses_duckdb_for_local_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-state.duckdb"
    settings = Settings(
        state_backend="duckdb",
        forecast_journal_path=str(path),
        historical_store_path=str(tmp_path / "history.duckdb"),
        source_sha="b" * 40,
    )
    service = install_orchestration_tools(
        _DummyMcp(),
        settings=settings,
        ydb_runtime=None,
    )
    assert isinstance(service.store, DuckDBOrchestrationStore)
    assert service.store.storage_scope == "local_file"

    run, created = service.start_core_validation(
        development_start="2021-01-01",
        split_date="2022-12-31",
        holdout_end="2024-12-31",
        source_sha="b" * 40,
    )
    assert created is True
    service.store.close()

    reopened = _open(path)
    try:
        assert reopened.get_workflow(run.workflow_id) is not None
    finally:
        reopened.close()
