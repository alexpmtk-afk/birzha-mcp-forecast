"""Durable workflow state for autonomous BIRZHA execution.

The orchestration layer is deliberately explicit: long-running work is split into
small idempotent actions, state is persisted, and protected gates cannot be
crossed without approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class WorkflowStage(StrEnum):
    HISTORY_PREPARATION = "HISTORY_PREPARATION"
    READINESS_AUDIT = "READINESS_AUDIT"
    SEALED_DEVELOPMENT = "SEALED_DEVELOPMENT"
    HOLDOUT_APPROVAL = "HOLDOUT_APPROVAL"
    HOLDOUT_EVALUATION = "HOLDOUT_EVALUATION"
    PROMOTION_APPROVAL = "PROMOTION_APPROVAL"
    PROMOTION = "PROMOTION"
    TEST_DEPLOYMENT = "TEST_DEPLOYMENT"
    E2E_VERIFICATION = "E2E_VERIFICATION"
    COMPLETE = "COMPLETE"


class WorkflowStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    FAILED = "FAILED"
    COMPLETE = "COMPLETE"


class ActionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    workflow_id: str
    kind: str
    stage: WorkflowStage
    status: WorkflowStatus
    created_at: str
    updated_at: str
    metadata: dict[str, object] = field(default_factory=dict)
    last_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "workflow_id": self.workflow_id,
            "kind": self.kind,
            "stage": self.stage.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class WorkflowAction:
    action_id: str
    workflow_id: str
    stage: WorkflowStage
    kind: str
    sequence: int
    payload: dict[str, object]
    status: ActionStatus = ActionStatus.PENDING
    attempt: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_until: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    last_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "workflow_id": self.workflow_id,
            "stage": self.stage.value,
            "kind": self.kind,
            "sequence": self.sequence,
            "payload": dict(self.payload),
            "status": self.status.value,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "lease_owner": self.lease_owner,
            "lease_until": self.lease_until,
            "evidence": dict(self.evidence),
            "last_error": self.last_error,
        }
