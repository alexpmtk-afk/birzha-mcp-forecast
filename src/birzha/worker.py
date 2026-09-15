"""Private HTTP worker invoked by a cloud scheduler.

YDB is the authoritative machine store. Every persistent D1 archive refresh must
also pass stable Google Bridge Protocol v1 staging, commit, and read-back parity
before its action can PASS. Archive maintenance is separate from model
validation and never opens holdout or promotion gates.
"""

from __future__ import annotations

import os
from uuid import uuid4

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from birzha.application.historical_data import HistoricalDataService
from birzha.application.history_policy import D1_ARCHIVE_START, latest_safe_d1_calendar_date
from birzha.application.market_data import MarketDataService
from birzha.application.market_mirror_sync import MarketMirrorSyncService
from birzha.application.orchestration_worker import OrchestrationWorker
from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.config import Settings
from birzha.domain.orchestration import WorkflowStatus
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge, GoogleSheetsBridgeConfig
from birzha.storage.ydb_runtime_orchestration_store import YdbRuntimeOrchestrationStore
from birzha.storage.ydb_runtime_storage import (
    YdbRuntimeHistoricalCandleStore,
    YdbRuntimeSlotPacingGate,
)
from birzha.storage.ydb_state import YdbRuntime
from birzha.version import SERVICE_NAME, VERSION


settings = Settings.from_env()
if settings.state_backend != "ydb" or not settings.ydb_connection_string:
    raise RuntimeError("autonomous worker requires BIRZHA_STATE_BACKEND=ydb")
if (os.getenv("BIRZHA_ORCHESTRATOR_WORKER") or "").strip().lower() != "true":
    raise RuntimeError("autonomous worker requires BIRZHA_ORCHESTRATOR_WORKER=true")
if not settings.source_sha:
    raise RuntimeError(
        "autonomous worker requires BIRZHA_SOURCE_SHA or BIRZHA_SOURCE_COMMIT"
    )

_runtime = YdbRuntime.connect(settings.ydb_connection_string)
_control = ProcessUpstreamControlPlane(
    gate_factory=lambda provider_key: YdbRuntimeSlotPacingGate(
        _runtime.pool,
        provider_key=provider_key,
    ),
    require_distributed_gate=True,
)
_market = MarketDataService.default(control_plane=_control)
_history_store = YdbRuntimeHistoricalCandleStore(_runtime.pool)
_history = HistoricalDataService(
    market_data=_market,
    store=_history_store,
)
_orchestrator = WorkflowOrchestrator(YdbRuntimeOrchestrationStore(_runtime.pool))

_mirror = None
if settings.market_mirror_bridge_url:
    if not (
        settings.market_mirror_bridge_secret
        and settings.market_mirror_root_folder_id
    ):
        raise RuntimeError("Google market mirror Bridge v1 configuration is incomplete")
    _bridge = GoogleSheetsBridge(
        GoogleSheetsBridgeConfig(
            bridge_url=settings.market_mirror_bridge_url,
            bridge_secret=settings.market_mirror_bridge_secret,
            root_folder_id=settings.market_mirror_root_folder_id,
        )
    )
    # Startup must fail closed before any scheduled work if the configured
    # deployment is not the stable isolated Birzha Bridge/root.
    _bridge.health()
    _mirror = MarketMirrorSyncService(source=_history_store, bridge=_bridge)
elif settings.market_mirror_required:
    raise RuntimeError("mandatory Google market mirror Bridge v1 is not configured")

_worker = OrchestrationWorker(
    orchestrator=_orchestrator,
    history=_history,
    mirror=_mirror,
    require_market_mirror=settings.market_mirror_required,
)


async def healthz(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": f"{SERVICE_NAME}-orchestrator-worker",
            "version": VERSION,
            "state_backend": "ydb",
            "mode": "private-autonomous-worker",
            "source_sha": settings.source_sha,
            "d1_archive_start": D1_ARCHIVE_START,
            "d1_archive_policy": "fixed_start_append_forward",
            "intraday_persistence": "forbidden",
            "market_mirror_required": settings.market_mirror_required,
            "market_mirror_configured": _mirror is not None,
            "market_mirror_protocol": 1 if _mirror is not None else None,
            "market_mirror_bridge_release": "1.0.0" if _mirror is not None else None,
        }
    )


async def tick(_: Request) -> JSONResponse:
    worker_id = f"timer-{uuid4().hex}"
    archive_end = latest_safe_d1_calendar_date()
    archive_run, created = _orchestrator.start_d1_archive_refresh(
        archive_start=D1_ARCHIVE_START,
        archive_end=archive_end,
        source_sha=settings.source_sha or "",
    )

    if archive_run.status == WorkflowStatus.FAILED:
        return JSONResponse(
            {
                "worker_status": "D1_ARCHIVE_REFRESH_FAILED",
                "archive_created": created,
                "archive_end": archive_end,
                "archive_workflow": _orchestrator.status(archive_run.workflow_id),
            }
        )
    if archive_run.status == WorkflowStatus.RUNNING:
        result = _worker.run_once(archive_run.workflow_id, worker_id=worker_id)
        return JSONResponse(
            {
                "archive_created": created,
                "archive_end": archive_end,
                **result,
            }
        )

    # Today's archive is already complete. Only then may the autonomous worker
    # continue any older safe workflow. Protected stages remain blocked by the
    # orchestration worker as before.
    result = _worker.run_next_active(worker_id=worker_id)
    return JSONResponse(
        {
            "archive_created": created,
            "archive_end": archive_end,
            "archive_status": archive_run.status.value,
            **result,
        }
    )


app = Starlette(
    debug=False,
    routes=[
        Route("/", tick, methods=["POST"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/tick", tick, methods=["POST"]),
    ],
)


def main() -> None:
    """Run the private worker ASGI application for the serverless container."""
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
