"""Executable entry point for Cloud Run and local development."""

from __future__ import annotations

import uvicorn

from birzha.config import Settings
from birzha.mcp.server import app


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
