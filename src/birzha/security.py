"""Minimal bearer-token protection for the MCP HTTP surface."""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class McpBearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, token: str) -> None:
        super().__init__(app)
        if not token:
            raise ValueError("MCP bearer token must be non-empty")
        self._expected = f"Bearer {token}"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/mcp" or path.startswith("/mcp/"):
            supplied = request.headers.get("authorization", "")
            if not hmac.compare_digest(supplied, self._expected):
                return JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)
