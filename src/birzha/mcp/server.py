"""BIRZHA MCP server surface.

MCP remains a thin interface: market logic lives in application/provider layers.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from birzha.application.market_data import MarketDataService
from birzha.config import Settings
from birzha.version import ARCHITECTURE_VERSION, SERVICE_NAME, VERSION

settings = Settings.from_env()
mcp = MCPServer(name=SERVICE_NAME, version=VERSION)
_market = MarketDataService.default()


@mcp.tool(name="system.version", description="Return BIRZHA MCP service version metadata.")
def system_version() -> dict[str, str]:
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "architecture": ARCHITECTURE_VERSION,
    }


@mcp.tool(
    name="market.resolve_active_future",
    description="Resolve the current liquid MOEX futures contract for a root symbol such as Si.",
)
def market_resolve_active_future(symbol: str) -> dict[str, object]:
    return _market.resolve(symbol).to_dict()


@mcp.tool(
    name="market.candles",
    description=(
        "Load real MOEX candles for the currently resolved futures contract. "
        "Supported timeframes: M1, M10, M15, H1, D1, W1, MN1."
    ),
)
def market_candles(
    symbol: str,
    timeframe: str,
    from_date: str,
    till_date: str,
    completed_only: bool = True,
) -> dict[str, object]:
    return _market.candles(
        symbol,
        timeframe=timeframe,
        from_date=from_date,
        till_date=till_date,
        completed_only=completed_only,
    ).to_dict()


@mcp.tool(
    name="market.recent_candles",
    description="Load recent real MOEX candles by lookback days for the current futures contract.",
)
def market_recent_candles(
    symbol: str,
    timeframe: str,
    lookback_days: int = 30,
    completed_only: bool = True,
) -> dict[str, object]:
    return _market.recent_candles(
        symbol,
        timeframe=timeframe,
        lookback_days=lookback_days,
        completed_only=completed_only,
    ).to_dict()


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": SERVICE_NAME, "version": VERSION})


transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=list(settings.mcp_allowed_hosts),
    allowed_origins=list(settings.mcp_allowed_origins),
)

app = mcp.streamable_http_app(
    json_response=True,
    transport_security=transport_security,
)
