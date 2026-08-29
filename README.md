# BIRZHA MCP Forecast System

Minimal MCP-M1 server foundation for the universal BIRZHA forecasting backend.

## Current scope

This repository intentionally contains **no market or forecast logic yet**.
MCP-M1 proves only the transport/runtime foundation:

- Python backend
- MCP Python SDK v2
- Streamable HTTP endpoint at `/mcp`
- liveness endpoint at `/healthz`
- MCP tool `system.version`
- Docker image suitable for the selected remote container runtime

## Local run

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m birzha.main
```

Then:

```bash
curl http://localhost:8080/healthz
```

MCP endpoint:

```text
http://localhost:8080/mcp
```

## First MCP contract

Tool:

```text
system.version
```

Expected structured result:

```json
{
  "service": "BIRZHA MCP FORECAST",
  "version": "0.1.0",
  "architecture": "BIRZHA_MCP_FORECAST_V0_2"
}
```

## Transport security

DNS-rebinding protection is enabled. Local development accepts only localhost/127.0.0.1.
Remote deployment must explicitly set `MCP_ALLOWED_HOSTS` to the Host header that is actually
observed through the Yandex API Gateway path. If an Origin header is expected, it must be explicitly
listed in `MCP_ALLOWED_ORIGINS`.

No permanent ChatGPT-to-MCP authentication scheme is introduced in M1.3. The first remote TEST is
restricted to the harmless `system.version` tool while the standard MCP-compatible authorization
flow is evaluated separately.

## Architecture rule

The `birzha.mcp` package is an adapter layer only. Instrument resolution, market data,
Snapshot, Data Quality, features, models, Journal and Outcome will live outside it.

## MCP-M1 acceptance path

```text
Git commit
-> Docker image
-> Yandex Container Registry
-> private Yandex Serverless Container
-> Yandex API Gateway
-> /healthz
-> /mcp
-> MCP discovery/tools/list
-> system.version
```

MOEX, ALGOPACK and Forecast logic are deliberately outside this gate.
