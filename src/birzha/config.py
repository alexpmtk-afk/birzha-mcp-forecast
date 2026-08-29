"""Runtime configuration for the MCP server foundation."""

from __future__ import annotations

import os
from dataclasses import dataclass


LOCAL_ALLOWED_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
)


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "0.0.0.0"
    port: int = 8080
    mcp_allowed_hosts: tuple[str, ...] = LOCAL_ALLOWED_HOSTS
    mcp_allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8080")),
            mcp_allowed_hosts=_csv_env("MCP_ALLOWED_HOSTS", LOCAL_ALLOWED_HOSTS),
            mcp_allowed_origins=_csv_env("MCP_ALLOWED_ORIGINS", ()),
        )
