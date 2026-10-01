from __future__ import annotations

from pathlib import Path

import pytest

from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.domain.orchestration import ActionStatus, WorkflowStage
from birzha.storage.duckdb_orchestration_store import DuckDBOrchestrationStore


def test_expired_action_is_reclaimed_after_reopen_and_fences_old_worker(
    tmp_path: Path,
) -> None:
    path = tmp_path / "orchestration.duckdb"
    first_store = DuckDBOrchestrationStore(str(path))
    run, _ = WorkflowOrchestrator(first_store).start_core_validation(
        development_start="2021-01-01",
        split_date="2022-12-31",
        holdout_end="2024-12-31",
        source_sha="c" * 40,
    )

    original = first_store.claim_next(
        run.workflow_id,
        stage=WorkflowStage.HISTORY_PREPARATION,
        worker_id="worker-before-restart",
        now="2030-01-02T10:00:00+00:00",
        lease_until="2030-01-02T10:00:10+00:00",
    )
    assert original is not None
    assert original.attempt == 1
    first_store.close()

    recovered_store = DuckDBOrchestrationStore(str(path))
    try:
        recovered = recovered_store.claim_next(
            run.workflow_id,
            stage=WorkflowStage.HISTORY_PREPARATION,
            worker_id="worker-after-restart",
            now="2030-01-02T10:00:11+00:00",
            lease_until="2030-01-02T10:07:11+00:00",
        )

        assert recovered is not None
        assert recovered.action_id == original.action_id
        assert recovered.attempt == 2
        assert recovered.lease_owner == "worker-after-restart"

        with pytest.raises(RuntimeError, match="lease ownership lost"):
            recovered_store.finish_action(
                original.action_id,
                worker_id="worker-before-restart",
                status=ActionStatus.PASS,
                evidence={"stale": True},
            )

        finished = recovered_store.finish_action(
            original.action_id,
            worker_id="worker-after-restart",
            status=ActionStatus.PASS,
            evidence={"recovered": True},
        )
        assert finished.status == ActionStatus.PASS
        assert finished.evidence == {"recovered": True}
    finally:
        recovered_store.close()
