"""Private HTTP worker invoked by a cloud scheduler.

This is intentionally separate from the public MCP surface. The worker discovers
safe runnable workflows from YDB, executes at most one bounded action per tick,
and persists the result before returning.
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
from birzha.application.market_data import MarketDataService
from birzha.application.market_mirror_sync import MarketMirrorSyncService
from birzha.application.orchestration_worker import OrchestrationWorker
from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.config import Settings
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge, GoogleSheetsBridgeConfig
from birzha.storage.ydb_runtime_storage import (
    YdbRuntimeHistoricalCandleStore,
    YdbRuntimeSlotPacingGate,
)
from birzha.storage.ydb_runtime_orchestration_store import YdbRuntimeOrchestrationStore
from birzha.storage.ydb_state import YdbRuntime
from birzha.version import SERVICE_NAME, VERSION


settings = Settings.from_env()
if settings.state_backend != "ydb" or not settings.ydb_connection_string:
    raise RuntimeError("autonomous worker requires BIRZHA_STATE_BACKEND=ydb")
if (os.getenv("BIRZHA_ORCHESTRATOR_WORKER") or "").strip().lower() != "true":
    raise RuntimeError("autonomous worker requires BIRZHA_ORCHESTRATOR_WORKER=true")

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
        raise RuntimeError("Google market mirror bridge configuration is incomplete")
    _bridge = GoogleSheetsBridge(
        GoogleSheetsBridgeConfig(
            bridge_url=settings.market_mirror_bridge_url,
            bridge_secret=settings.market_mirror_bridge_secret,
            root_folder_id=settings.market_mirror_root_folder_id,
        )
    )
    _mirror = MarketMirrorSyncService(source=_history_store, bridge=_bridge)

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
            "market_mirror_required": settings.market_mirror_required,
            "market_mirror_configured": _mirror is not None,
        }
    )


async def tick(_: Request) -> JSONResponse:
    worker_id = f"timer-{uuid4().hex}"
    result = _worker.run_next_active(worker_id=worker_id)
    return JSONResponse(result)


app = Starlette(
    debug=False,
    routes=[
        Route("/", tick, methods=["POST"]),
        Route("/healthz", healthz, methods=["GET"]),
        Route("/tick", tick, methods=["POST"]),
    ],
)


def main() -> None:
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
