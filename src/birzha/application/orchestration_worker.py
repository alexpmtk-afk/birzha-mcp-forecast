"""Execution worker for safe autonomous orchestration stages.

Only handlers proven safe for unattended execution are enabled here. Protected
or not-yet-integrated stages are reported as BLOCKED and are never claimed.
D1 finalization may additionally require the independent Google Sheets mirror.
"""

from __future__ import annotations

from dataclasses import dataclass

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.market_data import is_futures_root_symbol
from birzha.application.market_mirror_sync import MarketMirrorSyncService
from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.application.validation_readiness import (
    CORE_VALIDATION_SYMBOLS,
    ValidationDataReadinessService,
)
from birzha.domain.orchestration import WorkflowAction, WorkflowStatus


SAFE_UNATTENDED_KINDS = frozenset(
    {"HISTORY_SYNC_CHUNK", "HISTORY_FINALIZE_RANGE", "READINESS_AUDIT"}
)
DEFAULT_WORKER_LEASE_SECONDS = 180


@dataclass(slots=True)
class OrchestrationWorker:
    orchestrator: WorkflowOrchestrator
    history: HistoricalDataService
    mirror: MarketMirrorSyncService | None = None
    require_market_mirror: bool = False
    lease_seconds: int = DEFAULT_WORKER_LEASE_SECONDS

    def run_next_active(self, *, worker_id: str) -> dict[str, object]:
        active = self.orchestrator.store.list_workflows(active_only=True, limit=100)
        for run in reversed(active):
            if run.status != WorkflowStatus.RUNNING:
                continue
            state = self.orchestrator.status(run.workflow_id)
            next_action = state.get("next_action")
            if not isinstance(next_action, dict):
                continue
            kind = str(next_action.get("kind") or "")
            if kind not in SAFE_UNATTENDED_KINDS:
                continue
            return self.run_once(run.workflow_id, worker_id=worker_id)
        return {
            "worker_status": "NO_SAFE_RUNNABLE_WORK",
            "active_workflows": len(active),
        }

    def run_once(self, workflow_id: str, *, worker_id: str) -> dict[str, object]:
        state = self.orchestrator.status(workflow_id)
        if state["status"] != WorkflowStatus.RUNNING.value:
            return {"worker_status": "IDLE", "workflow": state}
        next_action = state.get("next_action")
        if not isinstance(next_action, dict):
            return {"worker_status": "IDLE", "workflow": state}
        kind = str(next_action.get("kind") or "")
        if kind not in SAFE_UNATTENDED_KINDS:
            return {
                "worker_status": "BLOCKED_NO_SAFE_HANDLER",
                "blocked_kind": kind,
                "workflow": state,
            }

        action = self.orchestrator.claim_next(
            workflow_id,
            worker_id=worker_id,
            lease_seconds=self.lease_seconds,
        )
        if action is None:
            return {
                "worker_status": "BUSY_OR_IDLE",
                "workflow": self.orchestrator.status(workflow_id),
            }

        try:
            evidence = self._execute(action)
        except Exception as exc:
            try:
                workflow = self.orchestrator.complete_action(
                    workflow_id,
                    action.action_id,
                    worker_id=worker_id,
                    passed=False,
                    evidence={"action_kind": action.kind, "error_type": type(exc).__name__},
                    error=f"{type(exc).__name__}:{exc}",
                )
            except RuntimeError:
                return {
                    "worker_status": "STALE_LEASE",
                    "action_id": action.action_id,
                    "action_kind": action.kind,
                    "workflow": self.orchestrator.status(workflow_id),
                }
            return {
                "worker_status": "ACTION_ERROR",
                "action_id": action.action_id,
                "action_kind": action.kind,
                "workflow": workflow,
            }

        try:
            workflow = self.orchestrator.complete_action(
                workflow_id,
                action.action_id,
                worker_id=worker_id,
                passed=True,
                evidence=evidence,
            )
        except RuntimeError:
            return {
                "worker_status": "STALE_LEASE",
                "action_id": action.action_id,
                "action_kind": action.kind,
                "workflow": self.orchestrator.status(workflow_id),
            }
        return {
            "worker_status": "ACTION_PASS",
            "action_id": action.action_id,
            "action_kind": action.kind,
            "evidence": evidence,
            "workflow": workflow,
        }

    def _execute(self, action: WorkflowAction) -> dict[str, object]:
        payload = action.payload
        if action.kind == "HISTORY_SYNC_CHUNK":
            result = self.history.sync(
                str(payload["symbol"]),
                timeframe=str(payload["timeframe"]),
                from_date=str(payload["from_date"]),
                till_date=str(payload["till_date"]),
            )
            return {
                "status": "PASS",
                "symbol": result.symbol,
                "timeframe": result.timeframe,
                "from_date": result.requested_from,
                "till_date": result.requested_till,
                "fetched_candles": result.fetched_candles,
                "stored_candles": result.stored_candles,
                "reused_verified_range": result.reused_verified_range,
            }
        if action.kind == "HISTORY_FINALIZE_RANGE":
            return self._finalize_history_range(payload)
        if action.kind == "READINESS_AUDIT":
            result = ValidationDataReadinessService(history=self.history).check(
                CORE_VALIDATION_SYMBOLS,
                validation_start=str(payload["validation_start"]),
                validation_end=str(payload["validation_end"]),
            )
            if result.status != "READY":
                raise RuntimeError(f"validation data readiness is {result.status}")
            return {"status": "PASS", "readiness": result.to_dict()}
        raise RuntimeError(f"unsupported unattended action: {action.kind}")

    def _finalize_history_range(self, payload: dict[str, object]) -> dict[str, object]:
        symbol = str(payload["symbol"])
        timeframe = str(payload["timeframe"]).upper()
        from_date = str(payload["from_date"])[:10]
        till_date = str(payload["till_date"])[:10]
        chunks_raw = payload.get("chunks")
        if not isinstance(chunks_raw, list) or not chunks_raw:
            raise RuntimeError("history finalizer requires non-empty chunk list")

        resolver_known = hasattr(self.history.market_data, "direct_resolver")
        root = is_futures_root_symbol(symbol)
        verification_symbol = (
            _verification_symbol(symbol, timeframe, is_root=root)
            if resolver_known
            else symbol
        )
        session_symbol = verification_symbol if resolver_known and timeframe == "D1" else symbol

        checked = 0
        for item in chunks_raw:
            if not isinstance(item, list) or len(item) != 2:
                raise RuntimeError("invalid history chunk descriptor")
            left, right = str(item[0])[:10], str(item[1])[:10]
            if not self.history.store.is_verified(
                verification_symbol, timeframe, left, right
            ):
                raise RuntimeError(
                    f"chunk verification missing for {verification_symbol} {timeframe} {left}..{right}"
                )
            if timeframe == "D1" and not self.history.store.is_session_range_verified(
                session_symbol, left, right
            ):
                raise RuntimeError(
                    f"session verification missing for {session_symbol} {left}..{right}"
                )
            checked += 1

        self.history.store.mark_verified(symbol, timeframe, from_date, till_date)
        if verification_symbol != symbol:
            self.history.store.mark_verified(
                verification_symbol, timeframe, from_date, till_date
            )
        if timeframe == "D1":
            self.history.store.mark_session_range_verified(
                session_symbol, from_date, till_date
            )

        mirror_evidence: dict[str, object] | None = None
        if timeframe == "D1":
            if self.require_market_mirror and self.mirror is None:
                raise RuntimeError("mandatory Google market mirror is not configured")
            if self.mirror is not None:
                mirror_evidence = self.mirror.sync(
                    symbol=symbol,
                    from_date=from_date,
                    till_date=till_date,
                )
                if mirror_evidence.get("status") != "MIRROR_SYNC_PASS":
                    raise RuntimeError("mandatory Google market mirror did not pass parity")

        result: dict[str, object] = {
            "status": "PASS",
            "symbol": symbol,
            "timeframe": timeframe,
            "from_date": from_date,
            "till_date": till_date,
            "verified_chunks": checked,
            "verification_symbol": verification_symbol,
        }
        if mirror_evidence is not None:
            result["market_mirror"] = mirror_evidence
        return result
