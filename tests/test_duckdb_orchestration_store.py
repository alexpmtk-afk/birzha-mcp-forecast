from __future__ import annotations

from pathlib import Path

from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.domain.orchestration import WorkflowStage, WorkflowStatus
from birzha.storage.duckdb_orchestration_store import DuckDBOrchestrationStore


def test_duckdb_orchestration_persists_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "state.duckdb"
    first = DuckDBOrchestrationStore(str(path))
    service = WorkflowOrchestrator(first)
    run, created = service.start_core_validation(
        development_start="2021-01-01",
        split_date="2022-12-31",
        holdout_end="2024-12-31",
        source_sha="a" * 40,
    )
    assert created is True
    workflow_id = run.workflow_id
    expected_action_count = len(first.list_actions(workflow_id))
    assert expected_action_count > 0
    first.close()

    second = DuckDBOrchestrationStore(str(path))
    restored = second.get_workflow(workflow_id)
    assert restored is not None
    assert restored.workflow_id == workflow_id
    assert restored.stage == WorkflowStage.HISTORY_PREPARATION
    assert restored.status == WorkflowStatus.RUNNING
    assert len(second.list_actions(workflow_id)) == expected_action_count
    second.close()


def test_duckdb_orchestration_claim_is_durable(tmp_path: Path) -> None:
    path = tmp_path / "state.duckdb"
    store = DuckDBOrchestrationStore(str(path))
    service = WorkflowOrchestrator(store)
    run, _ = service.start_core_validation(
        development_start="2021-01-01",
        split_date="2022-12-31",
        holdout_end="2024-12-31",
        source_sha="b" * 40,
    )
    action = service.claim_next(run.workflow_id, worker_id="worker-local")
    assert action is not None
    assert action.attempt == 1
    action_id = action.action_id
    store.close()

    reopened = DuckDBOrchestrationStore(str(path))
    restored = next(x for x in reopened.list_actions(run.workflow_id) if x.action_id == action_id)
    assert restored.attempt == 1
    assert restored.lease_owner == "worker-local"
    reopened.close()
