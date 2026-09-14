"""YDB-backed durable orchestration state and action queue."""

from __future__ import annotations

import json

import ydb

from birzha.domain.orchestration import (
    ActionStatus,
    WorkflowAction,
    WorkflowRun,
    WorkflowStage,
    WorkflowStatus,
)
from birzha.storage.orchestration_store import OrchestrationStore
from birzha.storage.ydb_state import QueryPool


class YdbOrchestrationStore(OrchestrationStore):
    storage_scope = "distributed"

    def __init__(self, pool: QueryPool, *, runs_table: str = "orchestration_runs", actions_table: str = "orchestration_actions") -> None:
        self._pool = pool
        self._runs_table = _safe_table_name(runs_table)
        self._actions_table = _safe_table_name(actions_table)
        self._init_schema()

    def _init_schema(self) -> None:
        self._pool.execute_with_retries(f"""
            CREATE TABLE IF NOT EXISTS `{self._runs_table}` (
                workflow_id Utf8 NOT NULL, kind Utf8 NOT NULL, stage Utf8 NOT NULL,
                status Utf8 NOT NULL, created_at Utf8 NOT NULL, updated_at Utf8 NOT NULL,
                metadata_json Utf8 NOT NULL, last_error Utf8 NOT NULL,
                PRIMARY KEY (workflow_id)
            );
            """, retry_settings=ydb.RetrySettings(idempotent=True))
        self._pool.execute_with_retries(f"""
            CREATE TABLE IF NOT EXISTS `{self._actions_table}` (
                action_id Utf8 NOT NULL, workflow_id Utf8 NOT NULL, stage Utf8 NOT NULL,
                kind Utf8 NOT NULL, sequence Uint64 NOT NULL, payload_json Utf8 NOT NULL,
                status Utf8 NOT NULL, attempt Uint64 NOT NULL, max_attempts Uint64 NOT NULL,
                lease_owner Utf8 NOT NULL, lease_until Utf8 NOT NULL, evidence_json Utf8 NOT NULL,
                last_error Utf8 NOT NULL, PRIMARY KEY (action_id)
            );
            """, retry_settings=ydb.RetrySettings(idempotent=True))

    def create_workflow(self, run: WorkflowRun, actions: tuple[WorkflowAction, ...]) -> None:
        if not actions:
            raise ValueError("workflow must contain at least one action")
        row_type = (ydb.StructType().add_member("action_id", ydb.PrimitiveType.Utf8)
            .add_member("workflow_id", ydb.PrimitiveType.Utf8).add_member("stage", ydb.PrimitiveType.Utf8)
            .add_member("kind", ydb.PrimitiveType.Utf8).add_member("sequence", ydb.PrimitiveType.Uint64)
            .add_member("payload_json", ydb.PrimitiveType.Utf8).add_member("status", ydb.PrimitiveType.Utf8)
            .add_member("attempt", ydb.PrimitiveType.Uint64).add_member("max_attempts", ydb.PrimitiveType.Uint64)
            .add_member("lease_owner", ydb.PrimitiveType.Utf8).add_member("lease_until", ydb.PrimitiveType.Utf8)
            .add_member("evidence_json", ydb.PrimitiveType.Utf8).add_member("last_error", ydb.PrimitiveType.Utf8))
        rows = [_action_row(action) for action in actions]
        self._pool.execute_with_retries(f"""
            DECLARE $workflow_id AS Utf8; DECLARE $run_kind AS Utf8; DECLARE $run_stage AS Utf8;
            DECLARE $run_status AS Utf8; DECLARE $created_at AS Utf8; DECLARE $updated_at AS Utf8;
            DECLARE $metadata_json AS Utf8; DECLARE $run_last_error AS Utf8;
            DECLARE $rows AS List<Struct<action_id:Utf8,workflow_id:Utf8,stage:Utf8,kind:Utf8,
                sequence:Uint64,payload_json:Utf8,status:Utf8,attempt:Uint64,max_attempts:Uint64,
                lease_owner:Utf8,lease_until:Utf8,evidence_json:Utf8,last_error:Utf8>>;
            INSERT INTO `{self._actions_table}` (action_id,workflow_id,stage,kind,sequence,payload_json,status,attempt,max_attempts,lease_owner,lease_until,evidence_json,last_error)
            SELECT action_id,workflow_id,stage,kind,sequence,payload_json,status,attempt,max_attempts,lease_owner,lease_until,evidence_json,last_error FROM AS_TABLE($rows);
            INSERT INTO `{self._runs_table}` (workflow_id,kind,stage,status,created_at,updated_at,metadata_json,last_error)
            VALUES ($workflow_id,$run_kind,$run_stage,$run_status,$created_at,$updated_at,$metadata_json,$run_last_error);
            """, {"$workflow_id": _utf8(run.workflow_id), "$run_kind": _utf8(run.kind), "$run_stage": _utf8(run.stage.value), "$run_status": _utf8(run.status.value), "$created_at": _utf8(run.created_at), "$updated_at": _utf8(run.updated_at), "$metadata_json": _utf8(_json(run.metadata)), "$run_last_error": _utf8(run.last_error or ""), "$rows": (rows, ydb.ListType(row_type))}, retry_settings=ydb.RetrySettings(idempotent=False))

    def get_workflow(self, workflow_id: str) -> WorkflowRun | None:
        result = self._pool.execute_with_retries(f"DECLARE $workflow_id AS Utf8; SELECT * FROM `{self._runs_table}` WHERE workflow_id = $workflow_id;", {"$workflow_id": _utf8(workflow_id)}, retry_settings=ydb.RetrySettings(idempotent=True))
        row = _first_row(result)
        return None if row is None else _workflow_from_row(row)

    def list_workflows(self, *, active_only: bool = False, limit: int = 50) -> list[WorkflowRun]:
        if limit <= 0 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        if active_only:
            query = f"DECLARE $limit AS Uint64; SELECT * FROM `{self._runs_table}` WHERE status = 'RUNNING' OR status = 'WAITING_APPROVAL' ORDER BY updated_at DESC, workflow_id DESC LIMIT $limit;"
        else:
            query = f"DECLARE $limit AS Uint64; SELECT * FROM `{self._runs_table}` ORDER BY updated_at DESC, workflow_id DESC LIMIT $limit;"
        result = self._pool.execute_with_retries(query, {"$limit": _uint64(limit)}, retry_settings=ydb.RetrySettings(idempotent=True))
        return [_workflow_from_row(row) for row in _rows(result)]

    def update_workflow(self, run: WorkflowRun) -> None:
        self._pool.execute_with_retries(f"""DECLARE $workflow_id AS Utf8; DECLARE $stage AS Utf8; DECLARE $status AS Utf8; DECLARE $updated_at AS Utf8; DECLARE $metadata_json AS Utf8; DECLARE $last_error AS Utf8;
            UPDATE `{self._runs_table}` SET stage=$stage,status=$status,updated_at=$updated_at,metadata_json=$metadata_json,last_error=$last_error WHERE workflow_id=$workflow_id;""",
            {"$workflow_id":_utf8(run.workflow_id),"$stage":_utf8(run.stage.value),"$status":_utf8(run.status.value),"$updated_at":_utf8(run.updated_at),"$metadata_json":_utf8(_json(run.metadata)),"$last_error":_utf8(run.last_error or "")}, retry_settings=ydb.RetrySettings(idempotent=True))

    def list_actions(self, workflow_id: str, *, stage: WorkflowStage | None = None) -> list[WorkflowAction]:
        if stage is None:
            query=f"DECLARE $workflow_id AS Utf8; SELECT * FROM `{self._actions_table}` WHERE workflow_id=$workflow_id ORDER BY sequence,action_id;"
            params={"$workflow_id":_utf8(workflow_id)}
        else:
            query=f"DECLARE $workflow_id AS Utf8; DECLARE $stage AS Utf8; SELECT * FROM `{self._actions_table}` WHERE workflow_id=$workflow_id AND stage=$stage ORDER BY sequence,action_id;"
            params={"$workflow_id":_utf8(workflow_id),"$stage":_utf8(stage.value)}
        result=self._pool.execute_with_retries(query,params,retry_settings=ydb.RetrySettings(idempotent=True))
        return [_action_from_row(row) for row in _rows(result)]

    def claim_next(self, workflow_id: str, *, stage: WorkflowStage, worker_id: str, now: str, lease_until: str) -> WorkflowAction | None:
        self._pool.execute_with_retries(f"""DECLARE $workflow_id AS Utf8; DECLARE $stage AS Utf8; DECLARE $now AS Utf8;
            UPDATE `{self._actions_table}` SET status='FAILED',lease_owner='',lease_until='',last_error='lease expired after maximum attempts'
            WHERE workflow_id=$workflow_id AND stage=$stage AND status='RUNNING' AND lease_until <= $now AND attempt >= max_attempts;""",
            {"$workflow_id":_utf8(workflow_id),"$stage":_utf8(stage.value),"$now":_utf8(now)}, retry_settings=ydb.RetrySettings(idempotent=True))
        result=self._pool.execute_with_retries(f"""DECLARE $workflow_id AS Utf8; DECLARE $stage AS Utf8; DECLARE $now AS Utf8;
            SELECT candidate.* FROM `{self._actions_table}` AS candidate
            WHERE candidate.workflow_id=$workflow_id AND candidate.stage=$stage AND candidate.attempt < candidate.max_attempts
              AND (candidate.status='PENDING' OR (candidate.status='RUNNING' AND candidate.lease_until <= $now))
              AND NOT EXISTS (SELECT 1 FROM `{self._actions_table}` AS active WHERE active.workflow_id=$workflow_id AND active.stage=$stage AND active.status='RUNNING' AND active.lease_until > $now)
            ORDER BY candidate.sequence,candidate.action_id LIMIT 1;""",
            {"$workflow_id":_utf8(workflow_id),"$stage":_utf8(stage.value),"$now":_utf8(now)}, retry_settings=ydb.RetrySettings(idempotent=True))
        row=_first_row(result)
        if row is None:
            return None
        candidate=_action_from_row(row)
        self._pool.execute_with_retries(f"""DECLARE $action_id AS Utf8; DECLARE $now AS Utf8; DECLARE $worker_id AS Utf8; DECLARE $lease_until AS Utf8; DECLARE $attempt AS Uint64;
            UPDATE `{self._actions_table}` SET status='RUNNING',attempt=$attempt,lease_owner=$worker_id,lease_until=$lease_until,last_error=''
            WHERE action_id=$action_id AND attempt < max_attempts AND (status='PENDING' OR (status='RUNNING' AND lease_until <= $now));""",
            {"$action_id":_utf8(candidate.action_id),"$now":_utf8(now),"$worker_id":_utf8(worker_id),"$lease_until":_utf8(lease_until),"$attempt":_uint64(candidate.attempt+1)}, retry_settings=ydb.RetrySettings(idempotent=True))
        claimed=self._get_action(candidate.action_id)
        if claimed is None or claimed.status != ActionStatus.RUNNING or claimed.lease_owner != worker_id:
            return None
        return claimed

    def finish_action(self, action_id: str, *, worker_id: str, status: ActionStatus, evidence: dict[str, object] | None = None, last_error: str | None = None) -> WorkflowAction:
        if status not in {ActionStatus.PASS, ActionStatus.FAILED}:
            raise ValueError("finish_action status must be PASS or FAILED")
        current=self._get_action(action_id)
        if current is None:
            raise KeyError(action_id)
        if current.status != ActionStatus.RUNNING:
            raise RuntimeError(f"action is not RUNNING: {action_id}")
        if current.lease_owner != worker_id:
            raise RuntimeError(f"action lease ownership lost: {action_id}")
        self._pool.execute_with_retries(f"""DECLARE $action_id AS Utf8; DECLARE $worker_id AS Utf8; DECLARE $status AS Utf8; DECLARE $evidence_json AS Utf8; DECLARE $last_error AS Utf8;
            UPDATE `{self._actions_table}` SET status=$status,evidence_json=$evidence_json,last_error=$last_error,lease_owner='',lease_until=''
            WHERE action_id=$action_id AND status='RUNNING' AND lease_owner=$worker_id;""",
            {"$action_id":_utf8(action_id),"$worker_id":_utf8(worker_id),"$status":_utf8(status.value),"$evidence_json":_utf8(_json(evidence or {})),"$last_error":_utf8(last_error or "")}, retry_settings=ydb.RetrySettings(idempotent=True))
        updated=self._get_action(action_id)
        if updated is None:
            raise RuntimeError(f"action disappeared after finish: {action_id}")
        if updated.status != status or updated.lease_owner is not None:
            raise RuntimeError(f"action lease ownership lost: {action_id}")
        return updated

    def reset_action(self, action_id: str) -> WorkflowAction:
        current=self._get_action(action_id)
        if current is None:
            raise KeyError(action_id)
        if current.status != ActionStatus.FAILED:
            raise RuntimeError(f"only FAILED action can be reset: {action_id}")
        self._pool.execute_with_retries(f"DECLARE $action_id AS Utf8; UPDATE `{self._actions_table}` SET status='PENDING',lease_owner='',lease_until='' WHERE action_id=$action_id AND status='FAILED';", {"$action_id":_utf8(action_id)}, retry_settings=ydb.RetrySettings(idempotent=True))
        updated=self._get_action(action_id)
        if updated is None:
            raise RuntimeError(f"action disappeared after reset: {action_id}")
        return updated

    def _get_action(self, action_id: str) -> WorkflowAction | None:
        result=self._pool.execute_with_retries(f"DECLARE $action_id AS Utf8; SELECT * FROM `{self._actions_table}` WHERE action_id=$action_id;", {"$action_id":_utf8(action_id)}, retry_settings=ydb.RetrySettings(idempotent=True))
        row=_first_row(result)
        return None if row is None else _action_from_row(row)


def _json(value: object) -> str:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)

def _safe_table_name(value: str) -> str:
    if not value or any(not (char.isalnum() or char == "_") for char in value):
        raise ValueError("YDB table name must contain only letters, digits and underscore")
    return value

def _utf8(value: str): return (value,ydb.PrimitiveType.Utf8)
def _uint64(value: int): return (value,ydb.PrimitiveType.Uint64)

def _rows(result: object) -> list[object]:
    if result is None: return []
    result_sets=list(result) if not isinstance(result,list) else result
    rows=[]
    for result_set in result_sets: rows.extend(list(getattr(result_set,"rows",[]) or []))
    return rows

def _first_row(result: object) -> object | None:
    rows=_rows(result); return rows[0] if rows else None

def _value(row: object, field: str) -> object:
    if isinstance(row,dict): return row[field]
    try: return row[field]  # type: ignore[index]
    except (TypeError,KeyError): return getattr(row,field)

def _workflow_from_row(row: object) -> WorkflowRun:
    last_error=str(_value(row,"last_error"))
    return WorkflowRun(workflow_id=str(_value(row,"workflow_id")),kind=str(_value(row,"kind")),stage=WorkflowStage(str(_value(row,"stage"))),status=WorkflowStatus(str(_value(row,"status"))),created_at=str(_value(row,"created_at")),updated_at=str(_value(row,"updated_at")),metadata=json.loads(str(_value(row,"metadata_json")) or "{}"),last_error=last_error or None)

def _action_from_row(row: object) -> WorkflowAction:
    lease_owner=str(_value(row,"lease_owner")); lease_until=str(_value(row,"lease_until")); last_error=str(_value(row,"last_error"))
    return WorkflowAction(action_id=str(_value(row,"action_id")),workflow_id=str(_value(row,"workflow_id")),stage=WorkflowStage(str(_value(row,"stage"))),kind=str(_value(row,"kind")),sequence=int(_value(row,"sequence")),payload=json.loads(str(_value(row,"payload_json")) or "{}"),status=ActionStatus(str(_value(row,"status"))),attempt=int(_value(row,"attempt")),max_attempts=int(_value(row,"max_attempts")),lease_owner=lease_owner or None,lease_until=lease_until or None,evidence=json.loads(str(_value(row,"evidence_json")) or "{}"),last_error=last_error or None)

def _action_row(action: WorkflowAction) -> dict[str, object]:
    return {"action_id":action.action_id,"workflow_id":action.workflow_id,"stage":action.stage.value,"kind":action.kind,"sequence":action.sequence,"payload_json":_json(action.payload),"status":action.status.value,"attempt":action.attempt,"max_attempts":action.max_attempts,"lease_owner":action.lease_owner or "","lease_until":action.lease_until or "","evidence_json":_json(action.evidence),"last_error":action.last_error or ""}
