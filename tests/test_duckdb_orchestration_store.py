from __future__ import annotations

from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)
from birzha.storage.duckdb_orchestration_store import DuckDBOrchestrationStore


def _run() -> WorkflowRun:
    return WorkflowRun(
        workflow_id="wf-1",
        kind="CORE_VALIDATION_V1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        status=WorkflowStatus.RUNNING,
        created_at="2026-09-18T00:00:00+00:00",
        updated_at="2026-09-18T00:00:00+00:00",
        metadata={"source_sha": "a" * 40},
    )


def _action() -> WorkflowAction:
    return WorkflowAction(
        action_id="a-1",
        workflow_id="wf-1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        kind="HISTORY_SYNC_CHUNK",
        sequence=1,
        payload={"symbol": "SBER"},
    )


def test_duckdb_orchestration_survives_reopen(tmp_path):
    path = tmp_path / "orchestration.duckdb"
    store = DuckDBOrchestrationStore(str(path))
    store.create_workflow(_run(), (_action(),))

    claimed = store.claim_next(
        "wf-1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        worker_id="worker-1",
        now="2026-09-18T00:00:01+00:00",
        lease_until="2026-09-18T00:01:01+00:00",
    )
    assert claimed is not None
    assert claimed.status == ActionStatus.RUNNING
    assert claimed.attempt == 1

    finished = store.finish_action(
        "a-1",
        worker_id="worker-1",
        status=ActionStatus.PASS,
        evidence={"ok": True},
    )
    assert finished.status == ActionStatus.PASS
    store.close()

    reopened = DuckDBOrchestrationStore(str(path))
    assert reopened.get_workflow("wf-1") == _run()
    actions = reopened.list_actions("wf-1")
    assert len(actions) == 1
    assert actions[0].status == ActionStatus.PASS
    assert actions[0].evidence == {"ok": True}
    reopened.close()


def test_duckdb_orchestration_expired_final_lease_fails_closed(tmp_path):
    path = tmp_path / "orchestration.duckdb"
    store = DuckDBOrchestrationStore(str(path))
    action = WorkflowAction(
        action_id="a-1",
        workflow_id="wf-1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        kind="HISTORY_SYNC_CHUNK",
        sequence=1,
        payload={},
        status=ActionStatus.RUNNING,
        attempt=3,
        max_attempts=3,
        lease_owner="dead-worker",
        lease_until="2026-09-18T00:00:00+00:00",
    )
    store.create_workflow(_run(), (action,))
    assert store.claim_next(
        "wf-1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        worker_id="worker-2",
        now="2026-09-18T00:05:00+00:00",
        lease_until="2026-09-18T00:06:00+00:00",
    ) is None
    stored = store.list_actions("wf-1")[0]
    assert stored.status == ActionStatus.FAILED
    assert stored.last_error == "lease expired after maximum attempts"
    store.close()
