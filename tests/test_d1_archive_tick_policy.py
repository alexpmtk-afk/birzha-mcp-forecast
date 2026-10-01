from __future__ import annotations

import ast
from pathlib import Path

from birzha.application.orchestration_worker import OrchestrationWorker
from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.domain.orchestration import WorkflowStatus
from birzha.storage.orchestration_store import MemoryOrchestrationStore


def _complete_workflow(orchestrator: WorkflowOrchestrator, workflow_id: str) -> None:
    state = orchestrator.status(workflow_id)
    while state["status"] == WorkflowStatus.RUNNING.value:
        action = orchestrator.claim_next(workflow_id, worker_id="test-tick")
        assert action is not None
        state = orchestrator.complete_action(
            workflow_id,
            action.action_id,
            worker_id="test-tick",
            passed=True,
            evidence={"test": "complete"},
        )


def test_daily_tick_drains_oldest_archive_before_starting_newer_range() -> None:
    orchestrator = WorkflowOrchestrator(MemoryOrchestrationStore())
    worker = OrchestrationWorker(orchestrator=orchestrator, history=object())  # type: ignore[arg-type]

    oldest, _ = orchestrator.start_d1_archive_refresh(
        archive_start="2026-09-29",
        archive_end="2026-09-29",
        source_sha="a" * 40,
    )
    newer_backlog, _ = orchestrator.start_d1_archive_refresh(
        archive_start="2026-09-29",
        archive_end="2026-09-30",
        source_sha="a" * 40,
    )

    selected, created = worker.ensure_next_d1_archive_refresh(
        archive_start="2026-09-29",
        archive_end="2026-10-01",
        source_sha="a" * 40,
    )

    assert created is False
    assert selected.workflow_id == oldest.workflow_id
    assert len(orchestrator.store.list_workflows(active_only=True)) == 2
    _complete_workflow(orchestrator, oldest.workflow_id)

    selected_backlog, created_backlog = worker.ensure_next_d1_archive_refresh(
        archive_start="2026-09-29",
        archive_end="2026-10-01",
        source_sha="a" * 40,
    )

    assert created_backlog is False
    assert selected_backlog.workflow_id == newer_backlog.workflow_id
    _complete_workflow(orchestrator, newer_backlog.workflow_id)

    selected_current, created_current = worker.ensure_next_d1_archive_refresh(
        archive_start="2026-09-29",
        archive_end="2026-10-01",
        source_sha="a" * 40,
    )

    assert created_current is True
    assert selected_current.metadata["archive_end"] == "2026-10-01"



def test_timer_entrypoint_uses_ordered_archive_selection() -> None:
    worker_source = Path(__file__).resolve().parents[1] / "src" / "birzha" / "worker.py"
    tree = ast.parse(worker_source.read_text(encoding="utf-8"))
    tick = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "tick"
    )
    called_attributes = {
        node.func.attr
        for node in ast.walk(tick)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "ensure_next_d1_archive_refresh" in called_attributes
    assert "start_d1_archive_refresh" not in called_attributes
