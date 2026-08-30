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
- mandatory upper-level outbound API safety governor

## Outbound API safety contract

Every operation that can create external market-data traffic must go through the
application-level request governor before it reaches a provider adapter. There is
no force/ignore-limit mode.

For the initial MOEX policy BIRZHA deliberately reserves 10% headroom below its
own conservative ceilings:

- public ISS internal ceiling: 2 attempts/s; operating target: 1.8 attempts/s;
- authenticated / ALGOPACK internal ceiling: 1 attempt/s; operating target: 0.9 attempts/s;
- large commands are split into bounded batches using worst-case retry cost;
- each batch is paced and the next batch waits for the next scheduling window;
- retries consume the same budget and HTTP 429 / transient 5xx use bounded backoff;
- a logical operation cannot fan out without a finite request budget;
- concurrent commands share one pacing gate rather than creating independent limiters.

Process-local coordination is sufficient only for local/single-process tests.
Remote market-data enablement in a platform that can run more than one container
instance MUST provide a distributed pacing gate (or an equivalently strict global
coordination mechanism). The governor fails closed when distributed scope is
required but unavailable.

MOEX's public materials do not provide one stable universal numeric quota that
BIRZHA can treat as an SLA for every ISS/ALGOPACK endpoint. If MOEX publishes a
stricter account/endpoint-specific rule, that stricter rule supersedes these
internal defaults.

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
Remote deployment must explicitly set `MCP_ALLOWED_HOSTS` from the real Yandex API Gateway domain.
For the final Gateway binding, list exactly two Host values:

```text
<gateway-domain>,<gateway-domain>:*
```

The `:*` form is a wildcard for the port only; it is not a wildcard for sibling or arbitrary
`*.apigw.yandexcloud.net` domains. If an Origin header is expected, it must be explicitly listed in
`MCP_ALLOWED_ORIGINS`.

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
