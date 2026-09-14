"""Storage contract for durable workflow orchestration."""

from __future__ import annotations

from dataclasses import replace
from threading import Lock
from typing import Protocol

from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)


class OrchestrationStore(Protocol):
    storage_scope: str

    def create_workflow(self, run: WorkflowRun, actions: tuple[WorkflowAction, ...]) -> None: ...
    def get_workflow(self, workflow_id: str) -> WorkflowRun | None: ...
    def list_workflows(self, *, active_only: bool = False, limit: int = 50) -> list[WorkflowRun]: ...
    def update_workflow(self, run: WorkflowRun) -> None: ...
    def list_actions(self, workflow_id: str, *, stage: WorkflowStage | None = None) -> list[WorkflowAction]: ...
    def claim_next(self, workflow_id: str, *, stage: WorkflowStage, worker_id: str, now: str, lease_until: str) -> WorkflowAction | None: ...
    def finish_action(self, action_id: str, *, worker_id: str, status: ActionStatus, evidence: dict[str, object] | None = None, last_error: str | None = None) -> WorkflowAction: ...
    def reset_action(self, action_id: str) -> WorkflowAction: ...


class MemoryOrchestrationStore:
    storage_scope = "process-local"

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._actions: dict[str, WorkflowAction] = {}
        self._lock = Lock()

    def create_workflow(self, run: WorkflowRun, actions: tuple[WorkflowAction, ...]) -> None:
        with self._lock:
            if run.workflow_id in self._runs:
                raise ValueError(f"workflow already exists: {run.workflow_id}")
            duplicate = [item.action_id for item in actions if item.action_id in self._actions]
            if duplicate:
                raise ValueError(f"action already exists: {duplicate[0]}")
            self._runs[run.workflow_id] = run
            for item in actions:
                self._actions[item.action_id] = item

    def get_workflow(self, workflow_id: str) -> WorkflowRun | None:
        with self._lock:
            return self._runs.get(workflow_id)

    def list_workflows(self, *, active_only: bool = False, limit: int = 50) -> list[WorkflowRun]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            items = list(self._runs.values())
            if active_only:
                items = [item for item in items if item.status in {WorkflowStatus.RUNNING, WorkflowStatus.WAITING_APPROVAL}]
            return sorted(items, key=lambda item: (item.updated_at, item.workflow_id), reverse=True)[:limit]

    def update_workflow(self, run: WorkflowRun) -> None:
        with self._lock:
            if run.workflow_id not in self._runs:
                raise KeyError(run.workflow_id)
            self._runs[run.workflow_id] = run

    def list_actions(self, workflow_id: str, *, stage: WorkflowStage | None = None) -> list[WorkflowAction]:
        with self._lock:
            items = [item for item in self._actions.values() if item.workflow_id == workflow_id]
            if stage is not None:
                items = [item for item in items if item.stage == stage]
            return sorted(items, key=lambda item: (item.sequence, item.action_id))

    def claim_next(self, workflow_id: str, *, stage: WorkflowStage, worker_id: str, now: str, lease_until: str) -> WorkflowAction | None:
        with self._lock:
            for action_id, item in list(self._actions.items()):
                if item.workflow_id == workflow_id and item.stage == stage and item.status == ActionStatus.RUNNING and item.lease_until is not None and item.lease_until <= now and item.attempt >= item.max_attempts:
                    self._actions[action_id] = replace(item, status=ActionStatus.FAILED, lease_owner=None, lease_until=None, last_error="lease expired after maximum attempts")
            active = any(item.workflow_id == workflow_id and item.stage == stage and item.status == ActionStatus.RUNNING and item.lease_until is not None and item.lease_until > now for item in self._actions.values())
            if active:
                return None
            candidates = sorted((item for item in self._actions.values() if item.workflow_id == workflow_id and item.stage == stage and item.attempt < item.max_attempts and (item.status == ActionStatus.PENDING or (item.status == ActionStatus.RUNNING and item.lease_until is not None and item.lease_until <= now))), key=lambda item: (item.sequence, item.action_id))
            if not candidates:
                return None
            current = candidates[0]
            claimed = replace(current, status=ActionStatus.RUNNING, attempt=current.attempt + 1, lease_owner=worker_id, lease_until=lease_until, last_error=None)
            self._actions[current.action_id] = claimed
            return claimed

    def finish_action(self, action_id: str, *, worker_id: str, status: ActionStatus, evidence: dict[str, object] | None = None, last_error: str | None = None) -> WorkflowAction:
        if status not in {ActionStatus.PASS, ActionStatus.FAILED}:
            raise ValueError("finish_action status must be PASS or FAILED")
        with self._lock:
            current = self._actions[action_id]
            if current.status != ActionStatus.RUNNING:
                raise RuntimeError(f"action is not RUNNING: {action_id}")
            if current.lease_owner != worker_id:
                raise RuntimeError(f"action lease ownership lost: {action_id}")
            updated = replace(current, status=status, evidence=dict(evidence or {}), last_error=last_error, lease_owner=None, lease_until=None)
            self._actions[action_id] = updated
            return updated

    def reset_action(self, action_id: str) -> WorkflowAction:
        with self._lock:
            current = self._actions[action_id]
            if current.status != ActionStatus.FAILED:
                raise RuntimeError(f"only FAILED action can be reset: {action_id}")
            updated = replace(current, status=ActionStatus.PENDING, lease_owner=None, lease_until=None)
            self._actions[action_id] = updated
            return updated
