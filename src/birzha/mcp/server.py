"""Minimal MCP-M1 server surface.

No market, instrument, data-provider or forecast logic belongs in this module.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from birzha.version import ARCHITECTURE_VERSION, SERVICE_NAME, VERSION

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


# Cloud Run terminates traffic behind a managed reverse proxy. The MCP SDK's
# localhost-only DNS-rebinding default would otherwise reject the public Host header.
# This M1 skeleton is intentionally unauthenticated and must not be considered a
# production security posture; auth/host validation is a separate M1 gate.
transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

app = mcp.streamable_http_app(
    json_response=True,
    transport_security=transport_security,
)
