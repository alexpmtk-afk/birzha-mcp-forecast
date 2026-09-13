from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)
from birzha.storage.ydb_orchestration_store import YdbOrchestrationStore


class FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object] | None, object]] = []

    def execute_with_retries(self, query, parameters=None, **kwargs):
        self.calls.append((query, parameters, kwargs.get("retry_settings")))
        return []


def _run() -> WorkflowRun:
    now = datetime.now(UTC).isoformat()
    return WorkflowRun(
        workflow_id="w1",
        kind="CORE_VALIDATION_V1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        status=WorkflowStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


def _action() -> WorkflowAction:
    return WorkflowAction(
        action_id="w1:0001:HISTORY_PREPARATION",
        workflow_id="w1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        kind="HISTORY_SYNC_CHUNK",
        sequence=1,
        payload={"symbol": "SBER"},
    )


def test_ydb_workflow_bootstrap_writes_actions_and_run_in_one_query() -> None:
    pool = FakePool()
    store = YdbOrchestrationStore(pool)  # type: ignore[arg-type]
    assert len(pool.calls) == 2  # schema only

    store.create_workflow(_run(), (_action(),))
    assert len(pool.calls) == 3
    query = pool.calls[-1][0]
    assert "INSERT INTO `orchestration_actions`" in query
    assert "INSERT INTO `orchestration_runs`" in query
    assert "$rows AS List<Struct" in query


def test_ydb_claim_query_refuses_next_pending_while_live_stage_lease_exists() -> None:
    pool = FakePool()
    store = YdbOrchestrationStore(pool)  # type: ignore[arg-type]
    assert store.claim_next(
        "w1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        worker_id="worker-1",
        now="2026-09-13T10:00:00+00:00",
        lease_until="2026-09-13T10:30:00+00:00",
    ) is None
    expire_query = pool.calls[-2][0]
    select_query = pool.calls[-1][0]
    assert "attempt >= max_attempts" in expire_query
    assert "status = 'FAILED'" in expire_query
    assert "NOT EXISTS" in select_query
    assert "active.status = 'RUNNING'" in select_query
    assert "active.lease_until > $now" in select_query
    assert "candidate.attempt < candidate.max_attempts" in select_query


def test_ydb_finish_query_is_fenced_by_lease_owner() -> None:
    pool = FakePool()
    store = YdbOrchestrationStore(pool)  # type: ignore[arg-type]
    running = replace(
        _action(),
        status=ActionStatus.RUNNING,
        attempt=1,
        lease_owner="worker-1",
        lease_until="2026-09-13T10:30:00+00:00",
    )
    passed = replace(
        running,
        status=ActionStatus.PASS,
        lease_owner=None,
        lease_until=None,
    )
    states = iter((running, passed))
    store._get_action = lambda _action_id: next(states)  # type: ignore[method-assign]

    result = store.finish_action(
        running.action_id,
        worker_id="worker-1",
        status=ActionStatus.PASS,
    )
    assert result.status == ActionStatus.PASS
    query, params, _ = pool.calls[-1]
    assert "lease_owner = $worker_id" in query
    assert params is not None
    assert params["$worker_id"][0] == "worker-1"


def test_ydb_active_workflow_discovery_is_bounded() -> None:
    pool = FakePool()
    store = YdbOrchestrationStore(pool)  # type: ignore[arg-type]
    assert store.list_workflows(active_only=True, limit=25) == []
    query, params, _ = pool.calls[-1]
    assert "status = 'RUNNING' OR status = 'WAITING_APPROVAL'" in query
    assert params is not None
    assert "$limit" in params
