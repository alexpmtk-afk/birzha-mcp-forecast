from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ydb

from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)
from birzha.storage.ydb_orchestration_store import YdbOrchestrationStore


def _token(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("token file is empty")
    return value


def _tables(prefix: str) -> tuple[str, str]:
    safe = "".join(char for char in prefix if char.isalnum() or char == "_")
    if not safe or safe != prefix:
        raise ValueError("prefix must contain only letters, digits and underscore")
    return f"{safe}_runs", f"{safe}_actions"


def _connect(connection_string: str, token_file: str):
    driver = ydb.Driver(
        connection_string=connection_string,
        credentials=ydb.AccessTokenCredentials(_token(token_file)),
    )
    driver.wait(timeout=15, fail_fast=True)
    return driver, ydb.QuerySessionPool(driver)


def _run(prefix: str, suffix: str = "restart") -> WorkflowRun:
    now = datetime.now(UTC).isoformat()
    return WorkflowRun(
        workflow_id=f"{prefix}_{suffix}",
        kind="M24_YDB_SMOKE",
        stage=WorkflowStage.HISTORY_PREPARATION,
        status=WorkflowStatus.RUNNING,
        created_at=now,
        updated_at=now,
        metadata={"purpose": "temporary real-YDB orchestration smoke"},
    )


def _action(run: WorkflowRun, *, max_attempts: int = 3) -> WorkflowAction:
    return WorkflowAction(
        action_id=f"{run.workflow_id}_action",
        workflow_id=run.workflow_id,
        stage=run.stage,
        kind="M24_YDB_SMOKE_ACTION",
        sequence=1,
        payload={"side_effect": "none"},
        max_attempts=max_attempts,
    )


def _create(store: YdbOrchestrationStore, prefix: str) -> None:
    run = _run(prefix)
    action = _action(run)
    store.create_workflow(run, (action,))
    now = datetime.now(UTC)
    claimed = store.claim_next(
        run.workflow_id,
        stage=run.stage,
        worker_id="smoke-worker-old",
        now=now.isoformat(),
        lease_until=(now + timedelta(seconds=1)).isoformat(),
    )
    if claimed is None or claimed.lease_owner != "smoke-worker-old" or claimed.attempt != 1:
        raise RuntimeError("initial YDB lease claim failed")
    print("YDB_ORCHESTRATION_CREATE=PASS", flush=True)
    print("YDB_ORCHESTRATION_CHECKPOINT=PASS", flush=True)


def _resume(store: YdbOrchestrationStore, prefix: str) -> None:
    # This function is intentionally executed in a new Python process by CI.
    # State must therefore come only from YDB, not from process memory.
    run = _run(prefix)
    stored = store.get_workflow(run.workflow_id)
    if stored is None:
        raise RuntimeError("workflow checkpoint missing after process restart")
    before = store.list_actions(run.workflow_id)
    if len(before) != 1 or before[0].lease_owner != "smoke-worker-old" or before[0].attempt != 1:
        raise RuntimeError("persisted lease checkpoint mismatch")

    future = "2999-01-01T00:00:00+00:00"
    future_lease = "2999-01-01T00:30:00+00:00"
    reclaimed = store.claim_next(
        run.workflow_id,
        stage=run.stage,
        worker_id="smoke-worker-new",
        now=future,
        lease_until=future_lease,
    )
    if reclaimed is None or reclaimed.lease_owner != "smoke-worker-new" or reclaimed.attempt != 2:
        raise RuntimeError("expired lease was not durably reclaimed")

    try:
        store.finish_action(
            reclaimed.action_id,
            worker_id="smoke-worker-old",
            status=ActionStatus.PASS,
        )
    except RuntimeError:
        pass
    else:
        raise RuntimeError("stale worker unexpectedly completed reclaimed action")

    finished = store.finish_action(
        reclaimed.action_id,
        worker_id="smoke-worker-new",
        status=ActionStatus.PASS,
        evidence={"real_ydb": True, "process_restart": True},
    )
    if finished.status != ActionStatus.PASS:
        raise RuntimeError("new lease owner could not finish action")

    # Independent max-attempt proof: a crashed one-attempt action must become
    # FAILED after lease expiry and must never be reclaimed again.
    exhausted_run = _run(prefix, "exhausted")
    exhausted_action = _action(exhausted_run, max_attempts=1)
    store.create_workflow(exhausted_run, (exhausted_action,))
    initial = store.claim_next(
        exhausted_run.workflow_id,
        stage=exhausted_run.stage,
        worker_id="smoke-crash-worker",
        now="2026-01-01T00:00:00+00:00",
        lease_until="2026-01-01T00:00:01+00:00",
    )
    if initial is None or initial.attempt != 1:
        raise RuntimeError("max-attempt smoke initial claim failed")
    again = store.claim_next(
        exhausted_run.workflow_id,
        stage=exhausted_run.stage,
        worker_id="smoke-should-not-run",
        now=future,
        lease_until=future_lease,
    )
    if again is not None:
        raise RuntimeError("expired final attempt was reclaimed")
    exhausted = store.list_actions(exhausted_run.workflow_id)
    if len(exhausted) != 1 or exhausted[0].status != ActionStatus.FAILED:
        raise RuntimeError("expired final attempt was not failed closed")

    print("YDB_ORCHESTRATION_PROCESS_RESTART=PASS", flush=True)
    print("YDB_ORCHESTRATION_LEASE_RECLAIM=PASS", flush=True)
    print("YDB_ORCHESTRATION_STALE_WORKER_FENCE=PASS", flush=True)
    print("YDB_ORCHESTRATION_MAX_ATTEMPTS=PASS", flush=True)


def _cleanup(pool, runs_table: str, actions_table: str) -> None:
    errors: list[str] = []
    for table in (actions_table, runs_table):
        try:
            pool.execute_with_retries(
                f"DROP TABLE `{table}`;",
                retry_settings=ydb.RetrySettings(idempotent=True),
            )
        except Exception as exc:
            errors.append(f"{table}:{type(exc).__name__}")
    if errors:
        raise RuntimeError("cleanup failed: " + ",".join(errors))
    print("YDB_ORCHESTRATION_CLEANUP=PASS", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-YDB M24 durable orchestration smoke")
    parser.add_argument("--connection-string", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--phase", choices=("create", "resume", "cleanup"), required=True)
    args = parser.parse_args()

    runs_table, actions_table = _tables(args.prefix)
    driver, pool = _connect(args.connection_string, args.token_file)
    try:
        if args.phase == "cleanup":
            _cleanup(pool, runs_table, actions_table)
            return 0
        store = YdbOrchestrationStore(
            pool,
            runs_table=runs_table,
            actions_table=actions_table,
        )
        if args.phase == "create":
            _create(store, args.prefix)
        else:
            _resume(store, args.prefix)
        return 0
    finally:
        stop = getattr(pool, "stop", None) or getattr(pool, "close", None)
        if callable(stop):
            stop()
        driver.stop(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
