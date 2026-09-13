"""Fail-closed durable workflow state machine.

This service is intentionally independent from any chat session. A scheduler or
worker may call ``claim_next`` repeatedly; progress is reconstructed entirely
from the durable store. Protected holdout and promotion gates never auto-open.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)
from birzha.storage.orchestration_store import OrchestrationStore


CORE_VALIDATION_WORKFLOW = "CORE_VALIDATION_V1"
PROTECTED_GATES = frozenset(
    {WorkflowStage.HOLDOUT_APPROVAL, WorkflowStage.PROMOTION_APPROVAL}
)
STAGE_ORDER = (
    WorkflowStage.HISTORY_PREPARATION,
    WorkflowStage.READINESS_AUDIT,
    WorkflowStage.SEALED_DEVELOPMENT,
    WorkflowStage.HOLDOUT_APPROVAL,
    WorkflowStage.HOLDOUT_EVALUATION,
    WorkflowStage.PROMOTION_APPROVAL,
    WorkflowStage.PROMOTION,
    WorkflowStage.TEST_DEPLOYMENT,
    WorkflowStage.E2E_VERIFICATION,
    WorkflowStage.COMPLETE,
)


class OrchestrationGateError(RuntimeError):
    pass


class WorkflowOrchestrator:
    def __init__(self, store: OrchestrationStore) -> None:
        self.store = store

    def start_core_validation(
        self,
        *,
        development_start: str,
        split_date: str,
        holdout_end: str,
        source_sha: str,
    ) -> WorkflowRun:
        if not development_start < split_date < holdout_end:
            raise ValueError("expected development_start < split_date < holdout_end")
        now = _now()
        workflow_id = f"core-validation-{uuid4().hex}"
        metadata: dict[str, object] = {
            "development_start": development_start,
            "split_date": split_date,
            "holdout_end": holdout_end,
            "source_sha": source_sha,
            "approvals": [],
            "policy": "fail-closed; holdout and promotion require explicit approval",
        }
        run = WorkflowRun(
            workflow_id=workflow_id,
            kind=CORE_VALIDATION_WORKFLOW,
            stage=WorkflowStage.HISTORY_PREPARATION,
            status=WorkflowStatus.RUNNING,
            created_at=now,
            updated_at=now,
            metadata=metadata,
        )
        actions = _core_actions(workflow_id, development_start, split_date, holdout_end, source_sha)
        self.store.create_workflow(run, actions)
        return run

    def status(self, workflow_id: str) -> dict[str, object]:
        run = self._require(workflow_id)
        actions = self.store.list_actions(workflow_id)
        counts = {status.value: 0 for status in ActionStatus}
        for action in actions:
            counts[action.status.value] += 1
        current = [item for item in actions if item.stage == run.stage]
        return {
            **run.to_dict(),
            "storage_scope": self.store.storage_scope,
            "action_counts": counts,
            "current_stage_actions": [item.to_dict() for item in current],
            "next_action": _next_pending(current),
            "requires_approval": run.stage in PROTECTED_GATES,
        }

    def claim_next(
        self,
        workflow_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 900,
    ) -> WorkflowAction | None:
        if not worker_id.strip():
            raise ValueError("worker_id must be non-empty")
        if lease_seconds < 30 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 30 and 3600")
        run = self._require(workflow_id)
        if run.status != WorkflowStatus.RUNNING:
            return None
        if run.stage in PROTECTED_GATES or run.stage == WorkflowStage.COMPLETE:
            return None
        now_dt = datetime.now(UTC)
        return self.store.claim_next(
            workflow_id,
            stage=run.stage,
            worker_id=worker_id,
            now=now_dt.isoformat(),
            lease_until=(now_dt + timedelta(seconds=lease_seconds)).isoformat(),
        )

    def complete_action(
        self,
        workflow_id: str,
        action_id: str,
        *,
        passed: bool,
        evidence: dict[str, object] | None = None,
        error: str | None = None,
    ) -> dict[str, object]:
        run = self._require(workflow_id)
        action = _action_by_id(self.store.list_actions(workflow_id), action_id)
        if action.stage != run.stage:
            raise RuntimeError("action stage does not match current workflow stage")
        if passed:
            self.store.finish_action(
                action_id,
                status=ActionStatus.PASS,
                evidence=evidence,
            )
        else:
            failed = self.store.finish_action(
                action_id,
                status=ActionStatus.FAILED,
                evidence=evidence,
                last_error=(error or "action failed")[:1500],
            )
            if failed.attempt < failed.max_attempts:
                self.store.reset_action(action_id)
            else:
                run = replace(
                    run,
                    status=WorkflowStatus.FAILED,
                    updated_at=_now(),
                    last_error=(error or f"action exhausted retries: {action_id}")[:1500],
                )
                self.store.update_workflow(run)
                return self.status(workflow_id)
        self._advance_if_ready(workflow_id)
        return self.status(workflow_id)

    def approve(
        self,
        workflow_id: str,
        *,
        gate: str,
        approval_id: str,
    ) -> dict[str, object]:
        run = self._require(workflow_id)
        if run.status != WorkflowStatus.WAITING_APPROVAL or run.stage not in PROTECTED_GATES:
            raise OrchestrationGateError("workflow is not waiting at a protected gate")
        if gate != run.stage.value:
            raise OrchestrationGateError(
                f"approval gate mismatch: expected {run.stage.value}, got {gate}"
            )
        if not approval_id.strip():
            raise ValueError("approval_id must be non-empty")
        metadata = dict(run.metadata)
        approvals = list(metadata.get("approvals") or [])
        approvals.append(
            {
                "gate": run.stage.value,
                "approval_id": approval_id,
                "approved_at": _now(),
            }
        )
        metadata["approvals"] = approvals
        next_stage = _next_stage(run.stage)
        updated = replace(
            run,
            stage=next_stage,
            status=(
                WorkflowStatus.COMPLETE
                if next_stage == WorkflowStage.COMPLETE
                else WorkflowStatus.RUNNING
            ),
            updated_at=_now(),
            metadata=metadata,
            last_error=None,
        )
        self.store.update_workflow(updated)
        self._advance_if_ready(workflow_id)
        return self.status(workflow_id)

    def _advance_if_ready(self, workflow_id: str) -> None:
        while True:
            run = self._require(workflow_id)
            if run.status != WorkflowStatus.RUNNING:
                return
            if run.stage in PROTECTED_GATES:
                self.store.update_workflow(
                    replace(
                        run,
                        status=WorkflowStatus.WAITING_APPROVAL,
                        updated_at=_now(),
                    )
                )
                return
            if run.stage == WorkflowStage.COMPLETE:
                self.store.update_workflow(
                    replace(run, status=WorkflowStatus.COMPLETE, updated_at=_now())
                )
                return
            stage_actions = self.store.list_actions(workflow_id, stage=run.stage)
            if not stage_actions:
                raise RuntimeError(f"workflow stage has no executable actions: {run.stage.value}")
            if any(item.status == ActionStatus.FAILED for item in stage_actions):
                self.store.update_workflow(
                    replace(
                        run,
                        status=WorkflowStatus.FAILED,
                        updated_at=_now(),
                        last_error=f"stage contains failed action: {run.stage.value}",
                    )
                )
                return
            if any(item.status != ActionStatus.PASS for item in stage_actions):
                return
            next_stage = _next_stage(run.stage)
            next_status = (
                WorkflowStatus.WAITING_APPROVAL
                if next_stage in PROTECTED_GATES
                else WorkflowStatus.COMPLETE
                if next_stage == WorkflowStage.COMPLETE
                else WorkflowStatus.RUNNING
            )
            self.store.update_workflow(
                replace(
                    run,
                    stage=next_stage,
                    status=next_status,
                    updated_at=_now(),
                    last_error=None,
                )
            )
            if next_status != WorkflowStatus.RUNNING:
                return

    def _require(self, workflow_id: str) -> WorkflowRun:
        run = self.store.get_workflow(workflow_id)
        if run is None:
            raise KeyError(f"workflow not found: {workflow_id}")
        return run


def _core_actions(
    workflow_id: str,
    development_start: str,
    split_date: str,
    holdout_end: str,
    source_sha: str,
) -> tuple[WorkflowAction, ...]:
    specs = (
        (
            WorkflowStage.HISTORY_PREPARATION,
            "PREPARE_HISTORY",
            {
                "validation_start": development_start,
                "split_date": split_date,
                "validation_end": holdout_end,
                "source_sha": source_sha,
                "execution_policy": "checkpointed/chunked; never one opaque multi-hour request in production",
            },
        ),
        (
            WorkflowStage.READINESS_AUDIT,
            "READINESS_AUDIT",
            {
                "validation_start": development_start,
                "validation_end": holdout_end,
                "required_markets": ["SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"],
                "required_timeframes": ["D1", "H1", "M15"],
            },
        ),
        (
            WorkflowStage.SEALED_DEVELOPMENT,
            "SEALED_DEVELOPMENT",
            {
                "development_start": development_start,
                "split_date": split_date,
                "holdout_end": holdout_end,
                "holdout_open": False,
            },
        ),
        (
            WorkflowStage.HOLDOUT_EVALUATION,
            "ONE_SHOT_HOLDOUT",
            {"requires_fingerprints": True, "single_use": True},
        ),
        (
            WorkflowStage.PROMOTION,
            "PROMOTE_ACCEPTED_SOURCE",
            {"immutable_source_required": True, "requires_green_checks": True},
        ),
        (
            WorkflowStage.TEST_DEPLOYMENT,
            "DEPLOY_ACCEPTED_SOURCE_TO_TEST",
            {"immutable_source_required": True},
        ),
        (
            WorkflowStage.E2E_VERIFICATION,
            "AUTHENTICATED_E2E",
            {"fail_closed": True},
        ),
    )
    return tuple(
        WorkflowAction(
            action_id=f"{workflow_id}:{index:02d}:{stage.value}",
            workflow_id=workflow_id,
            stage=stage,
            kind=kind,
            sequence=index,
            payload=payload,
        )
        for index, (stage, kind, payload) in enumerate(specs, start=1)
    )


def _next_stage(stage: WorkflowStage) -> WorkflowStage:
    index = STAGE_ORDER.index(stage)
    if index + 1 >= len(STAGE_ORDER):
        return WorkflowStage.COMPLETE
    return STAGE_ORDER[index + 1]


def _next_pending(actions: list[WorkflowAction]) -> dict[str, object] | None:
    for item in actions:
        if item.status in {ActionStatus.PENDING, ActionStatus.RUNNING}:
            return item.to_dict()
    return None


def _action_by_id(actions: list[WorkflowAction], action_id: str) -> WorkflowAction:
    for item in actions:
        if item.action_id == action_id:
            return item
    raise KeyError(f"action not found: {action_id}")


def _now() -> str:
    return datetime.now(UTC).isoformat()
