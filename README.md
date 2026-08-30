# BIRZHA MCP Forecast System

Universal MCP backend for causal MOEX market analysis, forecasting, immutable forecast journaling, outcome evaluation and historical walk-forward validation.

## Current implemented scope

The current `main` line contains working application code for:

- MCP Streamable HTTP endpoint `/mcp` and liveness `/healthz`;
- Docker runtime and transport-security controls;
- mandatory outbound MOEX/ALGOPACK request governor with bounded retries, conservative pacing and strict `Retry-After` handling;
- real MOEX ISS candles and instrument resolution;
- historical futures contract resolution for causal replay;
- SBER/equity and futures-root routing without symbol-specific domain logic;
- D1/H1/M15 causal Market Snapshot;
- ALGOPACK TradeStats Delta and FUTOI/Open Interest as optional causal flow evidence;
- explainable baseline Forecast Engine for 5/10/20 exchange sessions;
- immutable Forecast Journal reference backend;
- append-only Outcome Journal and 5/10/20-session outcome evaluation;
- causal historical walk-forward validation and real-MOEX end-to-end validation evidence.

The baseline forecast is **not yet statistically calibrated or accepted as a production-quality model**. Real historical validation is implemented; large-sample model research/calibration remains a later gate.

## MCP tools

Current MCP surface includes:

- `system.version`
- `market.resolve_instrument`
- `market.resolve_active_future`
- `market.candles`
- `market.recent_candles`
- `market.flow`
- `market.snapshot`
- `forecast.build`
- `forecast.create`
- `forecast.get`
- `forecast.list`
- `outcome.evaluate`
- `outcome.list`
- `validation.walk_forward`

MCP remains a thin adapter. Domain, provider, forecast, persistence, outcome and validation logic lives outside the MCP package.

## Causality rules

- Forecast input is limited to information available at its T0.
- Forming bars are excluded from completed-only Snapshot data.
- Historical futures roots resolve to the contract that was actually relevant on the historical date.
- Flow rows later than T0 are excluded; timestamps that cannot be proven causal fail closed.
- Missing analytical data stays missing/`NULL`; it is never replaced with a synthetic zero.
- Forecast Records are immutable; outcomes are append-only.

## Outbound API safety contract

Every external market-data operation must go through the application-level request governor. There is no force/ignore-limit mode.

BIRZHA currently reserves 10% headroom below conservative internal ceilings:

- public ISS: internal ceiling 2 attempts/s, operating target 1.8 attempts/s;
- authenticated ALGOPACK: internal ceiling 1 attempt/s, operating target 0.9 attempts/s;
- large commands are split into bounded batches;
- retries consume the same bounded budget;
- `Retry-After` is never shortened;
- 429 without `Retry-After` uses a conservative cooldown;
- repeated/stalled pagination fails closed.

All public `iss.moex.com` traffic in one process shares one pacing gate. Authenticated `apim.moex.com` traffic uses its separate stricter profile.

Process-local coordination is not sufficient for arbitrary multi-instance remote scale-out. Remote market-data enablement must use a distributed/global limiter or an equivalently strict single-instance deployment constraint.

## Persistence status

DuckDB is the current local/reference Forecast and Outcome backend. It proves immutability, idempotency, collision detection, reopen persistence and append-only behavior, but `/tmp` in a serverless container is **not** accepted as production durable storage.

A separate experimental `m11-ydb-durable-state` branch exists, but it is not part of `main` and is not considered accepted until it is rebased, tested and formally promoted.

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

## Transport security

DNS-rebinding protection is enabled. Local development accepts only localhost/127.0.0.1.
Remote deployment must explicitly set `MCP_ALLOWED_HOSTS` from the real Yandex API Gateway domain.

For the final Gateway binding, list exactly:

```text
<gateway-domain>,<gateway-domain>:*
```

The `:*` suffix is a port wildcard only, not a hostname wildcard.

## Current acceptance boundary

Implemented and regression-tested through the real-MOEX M10 end-to-end validation path:

```text
MOEX data
-> causal historical T0
-> Snapshot
-> Forecast
-> immutable Forecast Record
-> future 5/10/20 exchange sessions
-> Outcome
-> validation metrics
```

This proves the path works on real market data. It does not by itself prove forecast quality; statistical model calibration and real forward validation remain separate acceptance gates.
