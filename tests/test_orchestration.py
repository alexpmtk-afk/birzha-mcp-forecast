from __future__ import annotations

import pytest

from birzha.application.orchestrator import OrchestrationGateError, WorkflowOrchestrator
from birzha.domain.orchestration import WorkflowStage, WorkflowStatus
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
    action = service.claim_next(workflow_id, worker_id="worker-test")
    assert action is not None
    return service.complete_action(
        workflow_id,
        action.action_id,
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
        action = service.claim_next(workflow_id, worker_id=f"worker-{attempt}")
        assert action is not None
        state = service.complete_action(
            workflow_id,
            action.action_id,
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
