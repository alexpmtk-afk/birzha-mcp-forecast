from __future__ import annotations

from birzha.application.orchestration_plan import CORE_SYMBOLS
from birzha.application.orchestrator import (
    D1_ARCHIVE_REFRESH_WORKFLOW,
    WorkflowOrchestrator,
)
from birzha.domain.orchestration import WorkflowStage, WorkflowStatus
from birzha.storage.orchestration_store import MemoryOrchestrationStore


def test_d1_archive_refresh_is_idempotent_and_contains_only_six_d1_actions() -> None:
    store = MemoryOrchestrationStore()
    service = WorkflowOrchestrator(store)

    first, created = service.start_d1_archive_refresh(
        archive_start="2021-01-01",
        archive_end="2026-09-14",
        source_sha="a" * 40,
    )
    second, created_again = service.start_d1_archive_refresh(
        archive_start="2021-01-01",
        archive_end="2026-09-14",
        source_sha="a" * 40,
    )

    assert created is True
    assert created_again is False
    assert second.workflow_id == first.workflow_id
    assert first.kind == D1_ARCHIVE_REFRESH_WORKFLOW

    actions = store.list_actions(first.workflow_id)
    assert len(actions) == len(CORE_SYMBOLS) == 6
    assert {action.kind for action in actions} == {"D1_ARCHIVE_SYNC"}
    assert {action.payload["timeframe"] for action in actions} == {"D1"}
    assert {action.payload["from_date"] for action in actions} == {"2021-01-01"}
    assert {action.payload["till_date"] for action in actions} == {"2026-09-14"}


def test_d1_archive_refresh_completes_without_entering_validation_or_holdout() -> None:
    service = WorkflowOrchestrator(MemoryOrchestrationStore())
    run, _ = service.start_d1_archive_refresh(
        archive_start="2021-01-01",
        archive_end="2026-09-14",
        source_sha="b" * 40,
    )

    state = service.status(run.workflow_id)
    assert state["stage"] == WorkflowStage.HISTORY_PREPARATION.value
    assert state["requires_approval"] is False

    while state["status"] == WorkflowStatus.RUNNING.value:
        action = service.claim_next(run.workflow_id, worker_id="archive-worker")
        assert action is not None
        state = service.complete_action(
            run.workflow_id,
            action.action_id,
            worker_id="archive-worker",
            passed=True,
            evidence={"ydb": "PASS", "google_parity": "PASS"},
        )

    assert state["stage"] == WorkflowStage.COMPLETE.value
    assert state["status"] == WorkflowStatus.COMPLETE.value
    assert state["requires_approval"] is False
