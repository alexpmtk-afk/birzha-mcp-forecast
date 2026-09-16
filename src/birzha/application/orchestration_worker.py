"""Execution worker for safe autonomous orchestration stages.

Only handlers proven safe for unattended execution are enabled here. Protected
or not-yet-integrated stages are reported as BLOCKED and are never claimed.
Persistent market-price work is D1-only and requires Google Bridge-v1 parity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.history_policy import require_persistent_price_timeframe
from birzha.application.market_data import is_futures_root_symbol
from birzha.application.market_mirror_sync import MarketMirrorSyncService
from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.application.validation_readiness import (
    CORE_VALIDATION_SYMBOLS,
    ValidationDataReadinessService,
)
from birzha.domain.orchestration import WorkflowAction, WorkflowStatus


SAFE_UNATTENDED_KINDS = frozenset(
    {"D1_ARCHIVE_SYNC", "HISTORY_SYNC_CHUNK", "HISTORY_FINALIZE_RANGE", "READINESS_AUDIT"}
)
# Re-query a bounded right-edge window before a full D1 range is certified.
# This repairs sessions that were absent from MOEX historical ISS during an
# earlier attempt but became visible later, without replaying the full archive.
D1_EDGE_REVALIDATION_DAYS = 7
# The production Serverless Container is allowed up to six minutes for a full
# Google mirror commit/read-back. Keep the durable lease longer than that so a
# later timer tick can never reclaim the same action while the first call is
# still legitimately running.
DEFAULT_WORKER_LEASE_SECONDS = 420


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
            if not _is_safe_unattended(next_action):
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
        if not _is_safe_unattended(next_action):
            return {
                "worker_status": "BLOCKED_NO_SAFE_HANDLER",
                "blocked_kind": str(next_action.get("kind") or ""),
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
        if action.kind == "D1_ARCHIVE_SYNC":
            return self._sync_d1_archive(payload)
        if action.kind == "HISTORY_SYNC_CHUNK":
            timeframe = require_persistent_price_timeframe(str(payload["timeframe"]))
            result = self.history.sync(
                str(payload["symbol"]),
                timeframe=timeframe,
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

    def _sync_d1_archive(self, payload: dict[str, object]) -> dict[str, object]:
        symbol = str(payload["symbol"])
        timeframe = require_persistent_price_timeframe(str(payload.get("timeframe") or "D1"))
        from_date = str(payload["from_date"])[:10]
        till_date = str(payload["till_date"])[:10]

        result = self.history.sync(
            symbol,
            timeframe=timeframe,
            from_date=from_date,
            till_date=till_date,
        )
        if self.mirror is None:
            raise RuntimeError("D1 archive refresh requires Google Bridge v1 mirror")
        mirror_evidence = self.mirror.sync(
            symbol=symbol,
            from_date=from_date,
            till_date=till_date,
        )
        if mirror_evidence.get("status") != "MIRROR_SYNC_PASS":
            raise RuntimeError("mandatory Google market mirror did not pass parity")
        return {
            "status": "PASS",
            "symbol": result.symbol,
            "timeframe": result.timeframe,
            "from_date": result.requested_from,
            "till_date": result.requested_till,
            "fetched_candles": result.fetched_candles,
            "stored_candles": result.stored_candles,
            "reused_verified_range": result.reused_verified_range,
            "market_mirror": mirror_evidence,
        }

    def _revalidate_d1_edge(
        self,
        *,
        symbol: str,
        timeframe: str,
        from_date: str,
        till_date: str,
        session_symbol: str,
    ) -> dict[str, object]:
        """Refresh a bounded right edge without trusting historical markers.

        Chunk verification markers can outlive an upstream publication delay.
        Calling the service's low-level resolver/contract sync path deliberately
        bypasses the store-first marker shortcut and asks MOEX which sessions are
        visible *now*. Any newly visible session is then fetched before the full
        range can be certified or mirrored.
        """
        finish = date.fromisoformat(till_date)
        archive_start = date.fromisoformat(from_date)
        edge_start = max(
            archive_start,
            finish - timedelta(days=D1_EDGE_REVALIDATION_DAYS - 1),
        )
        segments = self.history._segments(symbol, edge_start, finish)
        if not segments:
            raise RuntimeError(
                f"D1 edge revalidation produced no segments for {symbol} "
                f"{edge_start.isoformat()}..{till_date}"
            )

        fetched = 0
        stored = 0
        for instrument, left, right in segments:
            result = self.history._sync_contract(
                symbol,
                instrument,
                timeframe,
                left,
                right,
                session_symbol=session_symbol,
            )
            fetched += result.fetched_candles
            stored += result.stored_candles

        sessions = self.history.store.stored_sessions(
            session_symbol,
            edge_start.isoformat(),
            till_date,
        )
        if not sessions:
            raise RuntimeError(
                f"D1 edge revalidation found no verified sessions for {symbol} "
                f"{edge_start.isoformat()}..{till_date}"
            )
        return {
            "status": "PASS",
            "from_date": edge_start.isoformat(),
            "till_date": till_date,
            "fresh_segments": len(segments),
            "fetched_candles": fetched,
            "stored_candles": stored,
            "last_session": sessions[-1],
        }

    def _finalize_history_range(self, payload: dict[str, object]) -> dict[str, object]:
        symbol = str(payload["symbol"])
        timeframe = require_persistent_price_timeframe(str(payload["timeframe"]))
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
        session_symbol = verification_symbol if resolver_known else symbol

        # Fresh right-edge proof must happen before aggregate verified markers
        # or Google mirror commit. This heals stale negative knowledge created
        # when MOEX historical ISS had not published the final session yet.
        edge_evidence = self._revalidate_d1_edge(
            symbol=symbol,
            timeframe=timeframe,
            from_date=from_date,
            till_date=till_date,
            session_symbol=session_symbol,
        )

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
            if not self.history.store.is_session_range_verified(
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
        self.history.store.mark_session_range_verified(
            session_symbol, from_date, till_date
        )

        if self.require_market_mirror and self.mirror is None:
            raise RuntimeError("mandatory Google market mirror is not configured")
        mirror_evidence: dict[str, object] | None = None
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
            "edge_revalidation": edge_evidence,
        }
        if mirror_evidence is not None:
            result["market_mirror"] = mirror_evidence
        return result


def _is_safe_unattended(action: dict[str, object]) -> bool:
    kind = str(action.get("kind") or "")
    if kind not in SAFE_UNATTENDED_KINDS:
        return False
    if kind in {"D1_ARCHIVE_SYNC", "HISTORY_SYNC_CHUNK", "HISTORY_FINALIZE_RANGE"}:
        payload = action.get("payload")
        if not isinstance(payload, dict):
            return False
        return str(payload.get("timeframe") or "").strip().upper() == "D1"
    return True
