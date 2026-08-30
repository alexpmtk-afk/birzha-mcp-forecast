"""BIRZHA MCP server surface.

MCP remains a thin interface: market, flow, snapshot, forecast, outcome and
persistence logic lives in application/storage layers.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from birzha.application.flow import MarketFlowService
from birzha.application.forecast import ForecastService
from birzha.application.journal import ForecastJournalService
from birzha.application.market_data import MarketDataService
from birzha.application.outcome import OutcomeService
from birzha.application.snapshot import MarketSnapshotService
from birzha.config import Settings
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal
from birzha.version import ARCHITECTURE_VERSION, SERVICE_NAME, VERSION

settings = Settings.from_env()
mcp = MCPServer(name=SERVICE_NAME, version=VERSION)
_market = MarketDataService.default()
_flow = MarketFlowService.default()
_snapshot = MarketSnapshotService(market_data=_market, flow=_flow)
_forecast = ForecastService(snapshots=_snapshot)
_journal_store = DuckDBForecastJournal(settings.forecast_journal_path)
_outcome_store = DuckDBOutcomeJournal(settings.forecast_journal_path)
_journal = ForecastJournalService(forecasts=_forecast, journal=_journal_store)
_outcomes = OutcomeService(market_data=_market, forecasts=_journal_store, outcomes=_outcome_store)


@mcp.tool(name="system.version", description="Return BIRZHA MCP service version metadata.")
def system_version() -> dict[str, str]:
    return {"service": SERVICE_NAME, "version": VERSION, "architecture": ARCHITECTURE_VERSION}


@mcp.tool(name="market.resolve_instrument", description="Resolve a directly listed MOEX instrument such as SBER or the current liquid futures contract for a root such as Si.")
def market_resolve_instrument(symbol: str) -> dict[str, object]:
    return _market.resolve(symbol).to_dict()


@mcp.tool(name="market.resolve_active_future", description="Backward-compatible resolver for the current liquid MOEX futures root such as Si.")
def market_resolve_active_future(symbol: str) -> dict[str, object]:
    return _market.provider.resolve_active_future(symbol).to_dict()


@mcp.tool(name="market.candles", description="Load real MOEX candles for a resolved futures or equity instrument. Supported timeframes: M1, M10, M15, H1, D1, W1, MN1.")
def market_candles(symbol: str, timeframe: str, from_date: str, till_date: str, completed_only: bool = True) -> dict[str, object]:
    return _market.candles(symbol, timeframe=timeframe, from_date=from_date, till_date=till_date, completed_only=completed_only).to_dict()


@mcp.tool(name="market.recent_candles", description="Load recent real MOEX candles by lookback days for a supported instrument.")
def market_recent_candles(symbol: str, timeframe: str, lookback_days: int = 30, completed_only: bool = True) -> dict[str, object]:
    return _market.recent_candles(symbol, timeframe=timeframe, lookback_days=lookback_days, completed_only=completed_only).to_dict()


@mcp.tool(name="market.flow", description="Build real MOEX flow analytics from ALGOPACK TradeStats and, for futures, FUTOI: aggressive buy/sell volume, volume delta, value delta and open interest.")
def market_flow(symbol: str, from_date: str | None = None, till_date: str | None = None, lookback_days: int = 5) -> dict[str, object]:
    return _flow.build(symbol, from_date=from_date, till_date=till_date, lookback_days=lookback_days).to_dict()


@mcp.tool(name="market.snapshot", description="Build a causal D1/H1/M15 Market Snapshot from real MOEX price, volume, ALGOPACK Delta and applicable OI data at one forecast T0.")
def market_snapshot(symbol: str, as_of_date: str | None = None) -> dict[str, object]:
    return _snapshot.build(symbol, as_of_date=as_of_date).to_dict()


@mcp.tool(name="forecast.build", description="Build an explainable ex-ante BIRZHA baseline forecast without persistence. Use forecast.create for an operational forecast that must enter the journal.")
def forecast_build(symbol: str, as_of_date: str | None = None) -> dict[str, object]:
    return _forecast.build(symbol, as_of_date=as_of_date).to_dict()


@mcp.tool(name="forecast.create", description="Create an ex-ante forecast and append it immutably to the configured Forecast Journal. Identical duplicate writes are idempotent; conflicting content is rejected.")
def forecast_create(symbol: str, as_of_date: str | None = None) -> dict[str, object]:
    return _journal.create_and_save(symbol, as_of_date=as_of_date)


@mcp.tool(name="forecast.get", description="Read one immutable Forecast Record from the Forecast Journal by forecast_id.")
def forecast_get(forecast_id: str) -> dict[str, object]:
    record = _journal.get(forecast_id)
    return record if record is not None else {"forecast_id": forecast_id, "status": "NOT_FOUND"}


@mcp.tool(name="forecast.list", description="List recent immutable Forecast Records, optionally filtered by symbol.")
def forecast_list(limit: int = 20, symbol: str | None = None) -> dict[str, object]:
    return _journal.list_recent(limit=limit, symbol=symbol)


@mcp.tool(name="outcome.evaluate", description="Evaluate a stored forecast against future completed MOEX trading sessions and append newly matured 5/10/20-session outcomes without modifying the forecast.")
def outcome_evaluate(forecast_id: str) -> dict[str, object]:
    return _outcomes.evaluate(forecast_id).to_dict()


@mcp.tool(name="outcome.list", description="List append-only matured outcomes already recorded for one forecast.")
def outcome_list(forecast_id: str) -> dict[str, object]:
    records = _outcome_store.list_for_forecast(forecast_id)
    return {"forecast_id": forecast_id, "count": len(records), "outcomes": [item.to_dict() for item in records]}


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": SERVICE_NAME, "version": VERSION})


transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=list(settings.mcp_allowed_hosts),
    allowed_origins=list(settings.mcp_allowed_origins),
)

app = mcp.streamable_http_app(json_response=True, transport_security=transport_security)
