"""Executable entry point for Cloud Run and local development."""

from __future__ import annotations

import uvicorn

from birzha.config import Settings
from birzha.mcp import server
from birzha.mcp.orchestration_extension import install_orchestration_tools


# Install orchestration tools before the ASGI app starts serving requests. In
# YDB mode their state is durable and shared across server instances; locally a
# process-memory reference store is used for development only.
install_orchestration_tools(
    server.mcp,
    settings=server.settings,
    ydb_runtime=server._ydb_runtime,
)

app = server.app


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
