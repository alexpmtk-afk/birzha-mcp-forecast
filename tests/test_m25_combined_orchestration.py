from __future__ import annotations

from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.domain.orchestration import ActionStatus, WorkflowStage, WorkflowStatus
from birzha.storage.orchestration_store import MemoryOrchestrationStore
from birzha.storage.ydb_runtime_orchestration_store import YdbRuntimeOrchestrationStore


SOURCE_SHA = "1" * 40


def test_m23_m24_m25_combined_orchestration_starts_fail_closed() -> None:
    store = MemoryOrchestrationStore()
    orchestrator = WorkflowOrchestrator(store)
    run, created = orchestrator.start_core_validation(
        development_start="2021-01-01",
        split_date="2022-12-31",
        holdout_end="2024-12-31",
        source_sha=SOURCE_SHA,
    )
    assert created is True
    assert run.stage == WorkflowStage.HISTORY_PREPARATION
    assert run.status == WorkflowStatus.RUNNING
    state = orchestrator.status(run.workflow_id)
    assert state["storage_scope"] == "process-local"
    assert state["next_action"]["kind"] == "HISTORY_SYNC_CHUNK"
    assert state["requires_approval"] is False


def test_claim_is_leased_and_not_double_claimed() -> None:
    store = MemoryOrchestrationStore()
    orchestrator = WorkflowOrchestrator(store)
    run, _ = orchestrator.start_core_validation(
        development_start="2021-01-01",
        split_date="2022-12-31",
        holdout_end="2024-12-31",
        source_sha=SOURCE_SHA,
    )
    claimed = orchestrator.claim_next(run.workflow_id, worker_id="worker-a", lease_seconds=60)
    assert claimed is not None
    assert claimed.status == ActionStatus.RUNNING
    assert claimed.lease_owner == "worker-a"
    assert orchestrator.claim_next(run.workflow_id, worker_id="worker-b", lease_seconds=60) is None


def test_runtime_ydb_orchestration_store_skips_schema_bootstrap() -> None:
    class Pool:
        def execute_with_retries(self, *args, **kwargs):
            raise AssertionError("runtime constructor must not execute DDL")

    store = YdbRuntimeOrchestrationStore(Pool())
    assert store.storage_scope == "distributed"
