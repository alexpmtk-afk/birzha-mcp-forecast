"""Minimal MCP-M1 server surface.

No market, instrument, data-provider or forecast logic belongs in this module.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from birzha.config import Settings
from birzha.version import ARCHITECTURE_VERSION, SERVICE_NAME, VERSION

settings = Settings.from_env()
mcp = MCPServer(name=SERVICE_NAME, version=VERSION)


@mcp.tool(name="system.version", description="Return BIRZHA MCP service version metadata.")
def system_version() -> dict[str, str]:
    return {
        "service": SERVICE_NAME,
        "version": VERSION,
        "architecture": ARCHITECTURE_VERSION,
    }


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": SERVICE_NAME, "version": VERSION})


# DNS-rebinding protection remains enabled for all HTTP MCP traffic.
# Local development accepts localhost/127.0.0.1. Remote deployment must explicitly
# supply the observed Yandex API Gateway Host header through MCP_ALLOWED_HOSTS.
# Origins are denied when present unless explicitly listed in MCP_ALLOWED_ORIGINS.
transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=list(settings.mcp_allowed_hosts),
    allowed_origins=list(settings.mcp_allowed_origins),
)

app = mcp.streamable_http_app(
    json_response=True,
    transport_security=transport_security,
)
