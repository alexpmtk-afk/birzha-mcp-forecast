"""Fail-closed durable workflow state machine.

This service is intentionally independent from any chat session. A scheduler or
worker may call ``claim_next`` repeatedly; progress is reconstructed entirely
from the durable store. Protected holdout and promotion gates never auto-open.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from birzha.application.orchestration_plan import (
    build_core_validation_actions,
    build_d1_archive_refresh_actions,
)
from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)
from birzha.storage.orchestration_store import OrchestrationStore


CORE_VALIDATION_WORKFLOW = "CORE_VALIDATION_V1"
D1_ARCHIVE_REFRESH_WORKFLOW = "D1_ARCHIVE_REFRESH_V1"
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

    def start_d1_archive_refresh(
        self,
        *,
        archive_start: str,
        archive_end: str,
        source_sha: str,
    ) -> tuple[WorkflowRun, bool]:
        """Ensure one deterministic D1 archive refresh for this date/source.

        Archive refresh is not model validation: it contains no readiness,
        development, holdout or promotion stage. Once all six market actions
        pass YDB + Google parity, the workflow becomes COMPLETE directly.
        """
        if archive_end[:10] < archive_start[:10]:
            raise ValueError("archive_end must not be before archive_start")
        if not source_sha.strip():
            raise ValueError("source_sha must be non-empty")
        workflow_id = d1_archive_refresh_workflow_id(
            archive_start=archive_start,
            archive_end=archive_end,
            source_sha=source_sha,
        )
        existing = self.store.get_workflow(workflow_id)
        if existing is not None:
            _assert_same_archive_identity(
                existing,
                archive_start=archive_start,
                archive_end=archive_end,
                source_sha=source_sha,
            )
            return existing, False

        now = _now()
        metadata: dict[str, object] = {
            "archive_start": archive_start[:10],
            "archive_end": archive_end[:10],
            "source_sha": source_sha,
            "persistent_timeframe": "D1",
            "intraday_mode": "H1/M15_ON_DEMAND_NOT_PERSISTED",
            "policy": "YDB primary; Google Bridge v1 parity mandatory before PASS",
        }
        run = WorkflowRun(
            workflow_id=workflow_id,
            kind=D1_ARCHIVE_REFRESH_WORKFLOW,
            stage=WorkflowStage.HISTORY_PREPARATION,
            status=WorkflowStatus.RUNNING,
            created_at=now,
            updated_at=now,
            metadata=metadata,
        )
        actions = build_d1_archive_refresh_actions(
            workflow_id,
            archive_start,
            archive_end,
            source_sha,
        )
        try:
            self.store.create_workflow(run, actions)
        except Exception:
            winner = self.store.get_workflow(workflow_id)
            if winner is None:
                raise
            _assert_same_archive_identity(
                winner,
                archive_start=archive_start,
                archive_end=archive_end,
                source_sha=source_sha,
            )
            return winner, False
        return run, True

    def start_core_validation(
        self,
        *,
        development_start: str,
        split_date: str,
        holdout_end: str,
        source_sha: str,
    ) -> tuple[WorkflowRun, bool]:
        """Ensure exactly one durable workflow exists for this immutable source.

        Returns ``(run, created)``. The deterministic identity closes the race
        between two simultaneous start calls: both target the same primary key,
        and a loser re-reads the winner after a conflicting/ambiguous write.
        """
        if not development_start < split_date < holdout_end:
            raise ValueError("expected development_start < split_date < holdout_end")
        if not source_sha.strip():
            raise ValueError("source_sha must be non-empty")
        workflow_id = core_validation_workflow_id(
            development_start=development_start,
            split_date=split_date,
            holdout_end=holdout_end,
            source_sha=source_sha,
        )
        existing = self.store.get_workflow(workflow_id)
        if existing is not None:
            _assert_same_core_identity(
                existing,
                development_start=development_start,
                split_date=split_date,
                holdout_end=holdout_end,
                source_sha=source_sha,
            )
            return existing, False

        now = _now()
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
        actions = build_core_validation_actions(
            workflow_id,
            development_start,
            split_date,
            holdout_end,
            source_sha,
        )
        try:
            self.store.create_workflow(run, actions)
        except Exception:
            winner = self.store.get_workflow(workflow_id)
            if winner is None:
                raise
            _assert_same_core_identity(
                winner,
                development_start=development_start,
                split_date=split_date,
                holdout_end=holdout_end,
                source_sha=source_sha,
            )
            return winner, False
        return run, True

    def list(self, *, active_only: bool = False, limit: int = 50) -> dict[str, object]:
        runs = self.store.list_workflows(active_only=active_only, limit=limit)
        return {
            "count": len(runs),
            "active_only": active_only,
            "workflows": [self.status(item.workflow_id) for item in runs],
        }

    def status(self, workflow_id: str) -> dict[str, object]:
        run = self._require(workflow_id)
        actions = self.store.list_actions(workflow_id)
        counts = {status.value: 0 for status in ActionStatus}
        for action in actions:
            counts[action.status.value] += 1
        current = [item for item in actions if item.stage == run.stage]
        current_counts = {status.value: 0 for status in ActionStatus}
        for action in current:
            current_counts[action.status.value] += 1
        failed = [item.to_dict() for item in current if item.status == ActionStatus.FAILED][:5]
        return {
            **run.to_dict(),
            "storage_scope": self.store.storage_scope,
            "action_counts": counts,
            "current_stage_total": len(current),
            "current_stage_counts": current_counts,
            "current_stage_completed": current_counts[ActionStatus.PASS.value],
            "current_stage_failed": failed,
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
        claimed = self.store.claim_next(
            workflow_id,
            stage=run.stage,
            worker_id=worker_id,
            now=now_dt.isoformat(),
            lease_until=(now_dt + timedelta(seconds=lease_seconds)).isoformat(),
        )
        if claimed is None:
            self._advance_if_ready(workflow_id)
        return claimed

    def complete_action(
        self,
        workflow_id: str,
        action_id: str,
        *,
        worker_id: str,
        passed: bool,
        evidence: dict[str, object] | None = None,
        error: str | None = None,
    ) -> dict[str, object]:
        if not worker_id.strip():
            raise ValueError("worker_id must be non-empty")
        run = self._require(workflow_id)
        action = _action_by_id(self.store.list_actions(workflow_id), action_id)
        if action.stage != run.stage:
            raise RuntimeError("action stage does not match current workflow stage")
        if passed:
            self.store.finish_action(
                action_id,
                worker_id=worker_id,
                status=ActionStatus.PASS,
                evidence=evidence,
            )
        else:
            failed = self.store.finish_action(
                action_id,
                worker_id=worker_id,
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

            if run.kind == D1_ARCHIVE_REFRESH_WORKFLOW:
                if run.stage != WorkflowStage.HISTORY_PREPARATION:
                    raise RuntimeError("D1 archive workflow entered an invalid stage")
                self.store.update_workflow(
                    replace(
                        run,
                        stage=WorkflowStage.COMPLETE,
                        status=WorkflowStatus.COMPLETE,
                        updated_at=_now(),
                        last_error=None,
                    )
                )
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


def d1_archive_refresh_workflow_id(
    *,
    archive_start: str,
    archive_end: str,
    source_sha: str,
) -> str:
    canonical = "|".join(
        (
            D1_ARCHIVE_REFRESH_WORKFLOW,
            archive_start[:10],
            archive_end[:10],
            source_sha.strip().lower(),
        )
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"d1-archive-refresh-{digest}"


def core_validation_workflow_id(
    *,
    development_start: str,
    split_date: str,
    holdout_end: str,
    source_sha: str,
) -> str:
    canonical = "|".join(
        (
            CORE_VALIDATION_WORKFLOW,
            development_start[:10],
            split_date[:10],
            holdout_end[:10],
            source_sha.strip().lower(),
        )
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"core-validation-{digest}"


def _assert_same_archive_identity(
    run: WorkflowRun,
    *,
    archive_start: str,
    archive_end: str,
    source_sha: str,
) -> None:
    expected = {
        "archive_start": archive_start[:10],
        "archive_end": archive_end[:10],
        "source_sha": source_sha,
    }
    if run.kind != D1_ARCHIVE_REFRESH_WORKFLOW or any(
        run.metadata.get(key) != value for key, value in expected.items()
    ):
        raise RuntimeError("deterministic D1 archive workflow identity collision")


def _assert_same_core_identity(
    run: WorkflowRun,
    *,
    development_start: str,
    split_date: str,
    holdout_end: str,
    source_sha: str,
) -> None:
    expected = {
        "development_start": development_start,
        "split_date": split_date,
        "holdout_end": holdout_end,
        "source_sha": source_sha,
    }
    if run.kind != CORE_VALIDATION_WORKFLOW or any(
        run.metadata.get(key) != value for key, value in expected.items()
    ):
        raise RuntimeError("deterministic workflow identity collision")


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
