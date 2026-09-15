from __future__ import annotations

from datetime import date

from birzha.application.orchestration_plan import CORE_SYMBOLS, D1_ARCHIVE_CHUNK_DAYS
from birzha.application.orchestrator import (
    D1_ARCHIVE_REFRESH_WORKFLOW,
    WorkflowOrchestrator,
)
from birzha.domain.orchestration import WorkflowStage, WorkflowStatus
from birzha.storage.orchestration_store import MemoryOrchestrationStore


def test_d1_archive_refresh_is_idempotent_and_chunked_per_symbol() -> None:
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
    for symbol in CORE_SYMBOLS:
        symbol_actions = [a for a in actions if a.payload.get("symbol") == symbol]
        chunks = [a for a in symbol_actions if a.kind == "HISTORY_SYNC_CHUNK"]
        finalizers = [a for a in symbol_actions if a.kind == "HISTORY_FINALIZE_RANGE"]

        assert len(chunks) > 1
        assert len(finalizers) == 1
        assert {a.payload["timeframe"] for a in symbol_actions} == {"D1"}
        assert finalizers[0].payload["from_date"] == "2021-01-01"
        assert finalizers[0].payload["till_date"] == "2026-09-14"

        expected_left = date(2021, 1, 1)
        for chunk in chunks:
            left = date.fromisoformat(str(chunk.payload["from_date"]))
            right = date.fromisoformat(str(chunk.payload["till_date"]))
            assert left == expected_left
            assert 1 <= (right - left).days + 1 <= D1_ARCHIVE_CHUNK_DAYS
            expected_left = right.fromordinal(right.toordinal() + 1)
        assert expected_left.fromordinal(expected_left.toordinal() - 1) == date(2026, 9, 14)


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
