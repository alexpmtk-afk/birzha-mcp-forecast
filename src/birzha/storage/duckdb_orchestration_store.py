"""DuckDB-backed durable orchestration state for local single-host runtime."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import duckdb

from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)
from birzha.storage.orchestration_store import OrchestrationStore


class DuckDBOrchestrationStore(OrchestrationStore):
    """Durable local orchestration store.

    This backend is intended for one local BIRZHA runtime on a single host.
    A process lock serializes claims and updates inside the runtime process.
    """

    storage_scope = "local_file"

    def __init__(self, path: str) -> None:
        if not path or path == ":memory:":
            raise ValueError("DuckDB orchestration requires a persistent file path")
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(path)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS orchestration_runs (
                    workflow_id VARCHAR PRIMARY KEY,
                    kind VARCHAR NOT NULL,
                    stage VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    created_at VARCHAR NOT NULL,
                    updated_at VARCHAR NOT NULL,
                    metadata_json VARCHAR NOT NULL,
                    last_error VARCHAR NOT NULL
                )
            """)
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS orchestration_actions (
                    action_id VARCHAR PRIMARY KEY,
                    workflow_id VARCHAR NOT NULL,
                    stage VARCHAR NOT NULL,
                    kind VARCHAR NOT NULL,
                    sequence UBIGINT NOT NULL,
                    payload_json VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    attempt UBIGINT NOT NULL,
                    max_attempts UBIGINT NOT NULL,
                    lease_owner VARCHAR NOT NULL,
                    lease_until VARCHAR NOT NULL,
                    evidence_json VARCHAR NOT NULL,
                    last_error VARCHAR NOT NULL
                )
            """)

    def create_workflow(self, run: WorkflowRun, actions: tuple[WorkflowAction, ...]) -> None:
        if not actions:
            raise ValueError("workflow must contain at least one action")
        with self._lock:
            self._connection.execute("BEGIN TRANSACTION")
            try:
                existing = self._connection.execute(
                    "SELECT 1 FROM orchestration_runs WHERE workflow_id=?",
                    [run.workflow_id],
                ).fetchone()
                if existing is not None:
                    raise ValueError(f"workflow already exists: {run.workflow_id}")
                duplicate = self._connection.execute(
                    "SELECT action_id FROM orchestration_actions WHERE action_id IN (SELECT * FROM UNNEST(?)) LIMIT 1",
                    [[item.action_id for item in actions]],
                ).fetchone()
                if duplicate is not None:
                    raise ValueError(f"action already exists: {duplicate[0]}")
                self._connection.executemany(
                    """
                    INSERT INTO orchestration_actions
                    (action_id,workflow_id,stage,kind,sequence,payload_json,status,attempt,
                     max_attempts,lease_owner,lease_until,evidence_json,last_error)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [_action_row(item) for item in actions],
                )
                self._connection.execute(
                    """
                    INSERT INTO orchestration_runs
                    (workflow_id,kind,stage,status,created_at,updated_at,metadata_json,last_error)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    _run_row(run),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_workflow(self, workflow_id: str) -> WorkflowRun | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM orchestration_runs WHERE workflow_id=?",
                [workflow_id],
            ).fetchone()
        return None if row is None else _workflow_from_tuple(row)

    def list_workflows(self, *, active_only: bool = False, limit: int = 50) -> list[WorkflowRun]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        with self._lock:
            if active_only:
                rows = self._connection.execute(
                    """
                    SELECT * FROM orchestration_runs
                    WHERE status IN ('RUNNING','WAITING_APPROVAL')
                    ORDER BY updated_at DESC, workflow_id DESC LIMIT ?
                    """,
                    [limit],
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM orchestration_runs ORDER BY updated_at DESC, workflow_id DESC LIMIT ?",
                    [limit],
                ).fetchall()
        return [_workflow_from_tuple(row) for row in rows]

    def update_workflow(self, run: WorkflowRun) -> None:
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM orchestration_runs WHERE workflow_id=?",
                [run.workflow_id],
            ).fetchone()
            if exists is None:
                raise KeyError(run.workflow_id)
            self._connection.execute(
                """
                UPDATE orchestration_runs
                SET stage=?, status=?, updated_at=?, metadata_json=?, last_error=?
                WHERE workflow_id=?
                """,
                [
                    run.stage.value,
                    run.status.value,
                    run.updated_at,
                    _json(run.metadata),
                    run.last_error or "",
                    run.workflow_id,
                ],
            )

    def list_actions(self, workflow_id: str, *, stage: WorkflowStage | None = None) -> list[WorkflowAction]:
        with self._lock:
            if stage is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM orchestration_actions
                    WHERE workflow_id=? ORDER BY sequence, action_id
                    """,
                    [workflow_id],
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM orchestration_actions
                    WHERE workflow_id=? AND stage=? ORDER BY sequence, action_id
                    """,
                    [workflow_id, stage.value],
                ).fetchall()
        return [_action_from_tuple(row) for row in rows]

    def claim_next(
        self,
        workflow_id: str,
        *,
        stage: WorkflowStage,
        worker_id: str,
        now: str,
        lease_until: str,
    ) -> WorkflowAction | None:
        with self._lock:
            self._connection.execute(
                """
                UPDATE orchestration_actions
                SET status='FAILED', lease_owner='', lease_until='',
                    last_error='lease expired after maximum attempts'
                WHERE workflow_id=? AND stage=? AND status='RUNNING'
                  AND lease_until<>'' AND lease_until<=? AND attempt>=max_attempts
                """,
                [workflow_id, stage.value, now],
            )
            active = self._connection.execute(
                """
                SELECT 1 FROM orchestration_actions
                WHERE workflow_id=? AND stage=? AND status='RUNNING'
                  AND lease_until<>'' AND lease_until>?
                LIMIT 1
                """,
                [workflow_id, stage.value, now],
            ).fetchone()
            if active is not None:
                return None

            row = self._connection.execute(
                """
                SELECT * FROM orchestration_actions
                WHERE workflow_id=? AND stage=? AND attempt<max_attempts
                  AND (
                    status='PENDING'
                    OR (status='RUNNING' AND lease_until<>'' AND lease_until<=?)
                  )
                ORDER BY sequence, action_id
                LIMIT 1
                """,
                [workflow_id, stage.value, now],
            ).fetchone()
            if row is None:
                return None

            current = _action_from_tuple(row)
            self._connection.execute(
                """
                UPDATE orchestration_actions
                SET status='RUNNING', attempt=?, lease_owner=?, lease_until=?, last_error=''
                WHERE action_id=?
                """,
                [current.attempt + 1, worker_id, lease_until, current.action_id],
            )
            claimed = self._get_action(current.action_id)
            if claimed is None or claimed.lease_owner != worker_id:
                return None
            return claimed

    def finish_action(
        self,
        action_id: str,
        *,
        worker_id: str,
        status: ActionStatus,
        evidence: dict[str, object] | None = None,
        last_error: str | None = None,
    ) -> WorkflowAction:
        if status not in {ActionStatus.PASS, ActionStatus.FAILED}:
            raise ValueError("finish_action status must be PASS or FAILED")
        with self._lock:
            current = self._get_action(action_id)
            if current is None:
                raise KeyError(action_id)
            if current.status != ActionStatus.RUNNING:
                raise RuntimeError(f"action is not RUNNING: {action_id}")
            if current.lease_owner != worker_id:
                raise RuntimeError(f"action lease ownership lost: {action_id}")
            self._connection.execute(
                """
                UPDATE orchestration_actions
                SET status=?, evidence_json=?, last_error=?, lease_owner='', lease_until=''
                WHERE action_id=? AND status='RUNNING' AND lease_owner=?
                """,
                [
                    status.value,
                    _json(evidence or {}),
                    last_error or "",
                    action_id,
                    worker_id,
                ],
            )
            updated = self._get_action(action_id)
            if updated is None:
                raise RuntimeError(f"action disappeared after finish: {action_id}")
            return updated

    def reset_action(self, action_id: str) -> WorkflowAction:
        with self._lock:
            current = self._get_action(action_id)
            if current is None:
                raise KeyError(action_id)
            if current.status != ActionStatus.FAILED:
                raise RuntimeError(f"only FAILED action can be reset: {action_id}")
            self._connection.execute(
                """
                UPDATE orchestration_actions
                SET status='PENDING', lease_owner='', lease_until=''
                WHERE action_id=?
                """,
                [action_id],
            )
            updated = self._get_action(action_id)
            assert updated is not None
            return updated

    def _get_action(self, action_id: str) -> WorkflowAction | None:
        row = self._connection.execute(
            "SELECT * FROM orchestration_actions WHERE action_id=?",
            [action_id],
        ).fetchone()
        return None if row is None else _action_from_tuple(row)

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _run_row(run: WorkflowRun) -> list[object]:
    return [
        run.workflow_id,
        run.kind,
        run.stage.value,
        run.status.value,
        run.created_at,
        run.updated_at,
        _json(run.metadata),
        run.last_error or "",
    ]


def _action_row(action: WorkflowAction) -> list[object]:
    return [
        action.action_id,
        action.workflow_id,
        action.stage.value,
        action.kind,
        action.sequence,
        _json(action.payload),
        action.status.value,
        action.attempt,
        action.max_attempts,
        action.lease_owner or "",
        action.lease_until or "",
        _json(action.evidence),
        action.last_error or "",
    ]


def _workflow_from_tuple(row: tuple[object, ...]) -> WorkflowRun:
    return WorkflowRun(
        workflow_id=str(row[0]),
        kind=str(row[1]),
        stage=WorkflowStage(str(row[2])),
        status=WorkflowStatus(str(row[3])),
        created_at=str(row[4]),
        updated_at=str(row[5]),
        metadata=json.loads(str(row[6]) or "{}"),
        last_error=str(row[7]) or None,
    )


def _action_from_tuple(row: tuple[object, ...]) -> WorkflowAction:
    return WorkflowAction(
        action_id=str(row[0]),
        workflow_id=str(row[1]),
        stage=WorkflowStage(str(row[2])),
        kind=str(row[3]),
        sequence=int(row[4]),
        payload=json.loads(str(row[5]) or "{}"),
        status=ActionStatus(str(row[6])),
        attempt=int(row[7]),
        max_attempts=int(row[8]),
        lease_owner=str(row[9]) or None,
        lease_until=str(row[10]) or None,
        evidence=json.loads(str(row[11]) or "{}"),
        last_error=str(row[12]) or None,
    )
