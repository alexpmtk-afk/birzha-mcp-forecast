"""Runtime configuration for the BIRZHA MCP server."""

from __future__ import annotations

import os
from dataclasses import dataclass


LOCAL_ALLOWED_HOSTS = (
    "127.0.0.1",
    "127.0.0.1:*",
    "localhost",
    "localhost:*",
)
DEFAULT_JOURNAL_PATH = "/tmp/birzha_forecast_journal.duckdb"
DEFAULT_HISTORICAL_PATH = "/tmp/birzha_historical_data.duckdb"


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "0.0.0.0"
    port: int = 8080
    mcp_allowed_hosts: tuple[str, ...] = LOCAL_ALLOWED_HOSTS
    mcp_allowed_origins: tuple[str, ...] = ()
    state_backend: str = "duckdb"
    forecast_journal_path: str = DEFAULT_JOURNAL_PATH
    historical_store_path: str = DEFAULT_HISTORICAL_PATH
    ydb_connection_string: str | None = None
    require_mcp_auth: bool = False
    mcp_bearer_token: str | None = None
    source_sha: str | None = None
    market_mirror_required: bool = False
    market_mirror_bridge_url: str | None = None
    market_mirror_bridge_secret: str | None = None
    market_mirror_root_folder_id: str | None = None
    durable_local_orchestration: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        backend = os.getenv("BIRZHA_STATE_BACKEND", "duckdb").strip().lower()
        if backend not in {"duckdb", "ydb"}:
            raise ValueError("BIRZHA_STATE_BACKEND must be 'duckdb' or 'ydb'")
        ydb_connection = os.getenv("YDB_CONNECTION_STRING") or None
        if backend == "ydb" and not ydb_connection:
            raise ValueError("YDB_CONNECTION_STRING is required when BIRZHA_STATE_BACKEND=ydb")

        require_auth = _bool_env("BIRZHA_REQUIRE_MCP_AUTH", False)
        bearer_token = (os.getenv("BIRZHA_MCP_BEARER_TOKEN") or "").strip() or None
        if require_auth and not bearer_token:
            raise ValueError("BIRZHA_MCP_BEARER_TOKEN is required when BIRZHA_REQUIRE_MCP_AUTH=true")

        # Infra historically exposed BIRZHA_SOURCE_COMMIT while application
        # settings used BIRZHA_SOURCE_SHA. Accept both, preferring SOURCE_SHA,
        # so deterministic durable workflow identity matches the deployed image.
        source_sha = (
            (os.getenv("BIRZHA_SOURCE_SHA") or "").strip()
            or (os.getenv("BIRZHA_SOURCE_COMMIT") or "").strip()
            or None
        )
        if source_sha is not None and (
            len(source_sha) != 40
            or any(char not in "0123456789abcdefABCDEF" for char in source_sha)
        ):
            raise ValueError(
                "BIRZHA_SOURCE_SHA/BIRZHA_SOURCE_COMMIT must be a full 40-character Git commit SHA"
            )

        durable_local_orchestration = _bool_env("BIRZHA_DURABLE_LOCAL_ORCHESTRATION", False)

        mirror_required = _bool_env("BIRZHA_MARKET_MIRROR_REQUIRED", False)
        mirror_url = (os.getenv("BIRZHA_MARKET_MIRROR_BRIDGE_URL") or "").strip() or None
        mirror_secret = (os.getenv("BIRZHA_MARKET_MIRROR_BRIDGE_SECRET") or "").strip() or None
        mirror_root = (os.getenv("BIRZHA_MARKET_MIRROR_ROOT_FOLDER_ID") or "").strip() or None
        mirror_values = (mirror_url, mirror_secret, mirror_root)
        if any(mirror_values) and not all(mirror_values):
            raise ValueError(
                "BIRZHA market mirror requires URL, secret, and root folder id together"
            )
        if mirror_required and not all(mirror_values):
            raise ValueError(
                "BIRZHA_MARKET_MIRROR_REQUIRED=true requires complete Bridge v1 configuration"
            )

        return cls(
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8080")),
            mcp_allowed_hosts=_csv_env("MCP_ALLOWED_HOSTS", LOCAL_ALLOWED_HOSTS),
            mcp_allowed_origins=_csv_env("MCP_ALLOWED_ORIGINS", ()),
            state_backend=backend,
            forecast_journal_path=os.getenv("BIRZHA_FORECAST_JOURNAL_PATH", DEFAULT_JOURNAL_PATH),
            historical_store_path=os.getenv("BIRZHA_HISTORICAL_STORE_PATH", DEFAULT_HISTORICAL_PATH),
            ydb_connection_string=ydb_connection,
            require_mcp_auth=require_auth,
            mcp_bearer_token=bearer_token,
            source_sha=source_sha,
            market_mirror_required=mirror_required,
            market_mirror_bridge_url=mirror_url,
            market_mirror_bridge_secret=mirror_secret,
            market_mirror_root_folder_id=mirror_root,
            durable_local_orchestration=durable_local_orchestration,
        )
