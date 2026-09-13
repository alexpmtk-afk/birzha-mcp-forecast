from __future__ import annotations

from dataclasses import replace

import pytest

from birzha.application.orchestrator import OrchestrationGateError, WorkflowOrchestrator
from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)
from birzha.storage.orchestration_store import MemoryOrchestrationStore


def _start() -> tuple[WorkflowOrchestrator, str]:
    service = WorkflowOrchestrator(MemoryOrchestrationStore())
    run = service.start_core_validation(
        development_start="2021-01-01",
        split_date="2022-12-31",
        holdout_end="2024-12-31",
        source_sha="abc123",
    )
    return service, run.workflow_id


def _pass_one(service: WorkflowOrchestrator, workflow_id: str) -> dict[str, object]:
    worker_id = "worker-test"
    action = service.claim_next(workflow_id, worker_id=worker_id)
    assert action is not None
    return service.complete_action(
        workflow_id,
        action.action_id,
        worker_id=worker_id,
        passed=True,
        evidence={"proof": "PASS"},
    )


def _pass_stage(
    service: WorkflowOrchestrator,
    workflow_id: str,
    stage: WorkflowStage,
) -> dict[str, object]:
    state = service.status(workflow_id)
    assert state["stage"] == stage.value
    while state["stage"] == stage.value and state["status"] == WorkflowStatus.RUNNING.value:
        state = _pass_one(service, workflow_id)
    return state


def test_workflow_advances_automatically_but_stops_at_protected_gates() -> None:
    service, workflow_id = _start()

    state = service.status(workflow_id)
    assert state["stage"] == WorkflowStage.HISTORY_PREPARATION.value
    assert state["status"] == WorkflowStatus.RUNNING.value
    assert state["current_stage_total"] > 300

    state = _pass_stage(service, workflow_id, WorkflowStage.HISTORY_PREPARATION)
    assert state["stage"] == WorkflowStage.READINESS_AUDIT.value

    state = _pass_stage(service, workflow_id, WorkflowStage.READINESS_AUDIT)
    assert state["stage"] == WorkflowStage.SEALED_DEVELOPMENT.value

    state = _pass_stage(service, workflow_id, WorkflowStage.SEALED_DEVELOPMENT)
    assert state["stage"] == WorkflowStage.HOLDOUT_APPROVAL.value
    assert state["status"] == WorkflowStatus.WAITING_APPROVAL.value
    assert state["requires_approval"] is True
    assert service.claim_next(workflow_id, worker_id="worker-test") is None

    state = service.approve(
        workflow_id,
        gate=WorkflowStage.HOLDOUT_APPROVAL.value,
        approval_id="human-approval-holdout",
    )
    assert state["stage"] == WorkflowStage.HOLDOUT_EVALUATION.value
    assert state["status"] == WorkflowStatus.RUNNING.value

    state = _pass_stage(service, workflow_id, WorkflowStage.HOLDOUT_EVALUATION)
    assert state["stage"] == WorkflowStage.PROMOTION_APPROVAL.value
    assert state["status"] == WorkflowStatus.WAITING_APPROVAL.value

    state = service.approve(
        workflow_id,
        gate=WorkflowStage.PROMOTION_APPROVAL.value,
        approval_id="human-approval-promotion",
    )
    assert state["stage"] == WorkflowStage.PROMOTION.value

    state = _pass_stage(service, workflow_id, WorkflowStage.PROMOTION)
    assert state["stage"] == WorkflowStage.TEST_DEPLOYMENT.value
    state = _pass_stage(service, workflow_id, WorkflowStage.TEST_DEPLOYMENT)
    assert state["stage"] == WorkflowStage.E2E_VERIFICATION.value
    state = _pass_stage(service, workflow_id, WorkflowStage.E2E_VERIFICATION)
    assert state["stage"] == WorkflowStage.COMPLETE.value
    assert state["status"] == WorkflowStatus.COMPLETE.value


def test_wrong_gate_cannot_open_holdout() -> None:
    service, workflow_id = _start()
    _pass_stage(service, workflow_id, WorkflowStage.HISTORY_PREPARATION)
    _pass_stage(service, workflow_id, WorkflowStage.READINESS_AUDIT)
    _pass_stage(service, workflow_id, WorkflowStage.SEALED_DEVELOPMENT)

    with pytest.raises(OrchestrationGateError):
        service.approve(
            workflow_id,
            gate=WorkflowStage.PROMOTION_APPROVAL.value,
            approval_id="wrong-gate",
        )

    state = service.status(workflow_id)
    assert state["stage"] == WorkflowStage.HOLDOUT_APPROVAL.value
    assert state["status"] == WorkflowStatus.WAITING_APPROVAL.value


def test_action_retries_then_fails_closed() -> None:
    service, workflow_id = _start()

    for attempt in range(1, 4):
        worker_id = f"worker-{attempt}"
        action = service.claim_next(workflow_id, worker_id=worker_id)
        assert action is not None
        state = service.complete_action(
            workflow_id,
            action.action_id,
            worker_id=worker_id,
            passed=False,
            error=f"failure-{attempt}",
        )
        if attempt < 3:
            assert state["status"] == WorkflowStatus.RUNNING.value
            assert state["stage"] == WorkflowStage.HISTORY_PREPARATION.value
        else:
            assert state["status"] == WorkflowStatus.FAILED.value
            assert state["last_error"] == "failure-3"


def test_status_exposes_checkpointed_next_action_without_chat_memory() -> None:
    service, workflow_id = _start()
    state = service.status(workflow_id)
    next_action = state["next_action"]
    assert isinstance(next_action, dict)
    assert next_action["kind"] == "HISTORY_SYNC_CHUNK"
    assert next_action["payload"]["source_sha"] == "abc123"
    assert next_action["payload"]["symbol"] == "SBER"
    assert next_action["payload"]["timeframe"] == "D1"
    assert "current_stage_actions" not in state


def _minimal_run_and_action(*, max_attempts: int = 3) -> tuple[WorkflowRun, WorkflowAction]:
    run = WorkflowRun(
        workflow_id="lease-test",
        kind="TEST",
        stage=WorkflowStage.HISTORY_PREPARATION,
        status=WorkflowStatus.RUNNING,
        created_at="2026-09-13T10:00:00+00:00",
        updated_at="2026-09-13T10:00:00+00:00",
    )
    action = WorkflowAction(
        action_id="lease-test:1",
        workflow_id=run.workflow_id,
        stage=run.stage,
        kind="HISTORY_SYNC_CHUNK",
        sequence=1,
        payload={},
        max_attempts=max_attempts,
    )
    return run, action


def test_stale_worker_cannot_finish_after_lease_was_reclaimed() -> None:
    store = MemoryOrchestrationStore()
    run, action = _minimal_run_and_action()
    store.create_workflow(run, (action,))

    first = store.claim_next(
        run.workflow_id,
        stage=run.stage,
        worker_id="worker-old",
        now="2026-09-13T10:00:00+00:00",
        lease_until="2026-09-13T10:00:10+00:00",
    )
    assert first is not None
    second = store.claim_next(
        run.workflow_id,
        stage=run.stage,
        worker_id="worker-new",
        now="2026-09-13T10:00:11+00:00",
        lease_until="2026-09-13T10:00:21+00:00",
    )
    assert second is not None
    assert second.lease_owner == "worker-new"

    with pytest.raises(RuntimeError, match="lease ownership lost"):
        store.finish_action(
            action.action_id,
            worker_id="worker-old",
            status=ActionStatus.PASS,
        )

    finished = store.finish_action(
        action.action_id,
        worker_id="worker-new",
        status=ActionStatus.PASS,
    )
    assert finished.status == ActionStatus.PASS


def test_expired_final_lease_fails_closed_instead_of_reclaiming_forever() -> None:
    store = MemoryOrchestrationStore()
    run, action = _minimal_run_and_action(max_attempts=2)
    store.create_workflow(run, (action,))

    first = store.claim_next(
        run.workflow_id,
        stage=run.stage,
        worker_id="worker-1",
        now="2026-09-13T10:00:00+00:00",
        lease_until="2026-09-13T10:00:10+00:00",
    )
    assert first is not None and first.attempt == 1
    second = store.claim_next(
        run.workflow_id,
        stage=run.stage,
        worker_id="worker-2",
        now="2026-09-13T10:00:11+00:00",
        lease_until="2026-09-13T10:00:21+00:00",
    )
    assert second is not None and second.attempt == 2

    assert store.claim_next(
        run.workflow_id,
        stage=run.stage,
        worker_id="worker-3",
        now="2026-09-13T10:00:22+00:00",
        lease_until="2026-09-13T10:00:32+00:00",
    ) is None
    final = store.list_actions(run.workflow_id)[0]
    assert final.status == ActionStatus.FAILED
    assert final.attempt == 2
    assert final.last_error == "lease expired after maximum attempts"
