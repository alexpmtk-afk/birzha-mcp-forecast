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
- Docker/Cloud Run compatible entry point

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

## Architecture rule

The `birzha.mcp` package is an adapter layer only. Instrument resolution, market data,
Snapshot, Data Quality, features, models, Journal and Outcome will live outside it.

## MCP-M1.1 acceptance target

```text
ChatGPT -> MCP Streamable HTTP -> BIRZHA backend -> system.version -> structured result
```
