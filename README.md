# BIRZHA MCP Forecast System

Universal MCP backend for causal MOEX market data, forecasting, immutable forecast journaling, outcome evaluation and historical validation.

## Current implemented scope

The current `main` line contains working application code for:

- MCP Streamable HTTP endpoint `/mcp` and liveness `/healthz`;
- Docker runtime and transport-security controls;
- mandatory outbound MOEX/ALGOPACK request governor with bounded retries, conservative pacing and strict `Retry-After` handling;
- real MOEX ISS candles and instrument resolution;
- historical futures contract resolution for causal replay;
- configured core instruments SBER, Si, BR, GOLD, IMOEX and RTSI, through shared instrument and futures-root routing;
- D1/H1/M15 causal Market Snapshot;
- ALGOPACK TradeStats Delta and applicable FUTOI/Open Interest, plus persisted TradeStats-compatible rows derived from public MOEX trades where supported;
- explainable technical baseline forecast for 5/10/20 exchange sessions;
- Prediction Contract and Outcome Contract tools;
- immutable Forecast Journal reference backend;
- append-only Outcome Journal and 5/10/20-session outcome evaluation;
- causal historical walk-forward validation and real-MOEX end-to-end validation evidence.

The baseline forecast is **not yet statistically calibrated or accepted as a production-quality model**. Real historical validation is implemented; large-sample model research/calibration remains a later gate.

## MCP tools

Current MCP surface includes:

- System: `system.version`.
- Market: `market.resolve_instrument`, `market.resolve_active_future`, `market.candles`, `market.recent_candles`, `market.flow`, `market.snapshot`.
- History: `history.sync`, `history.sync_batch`, `history.sync_core`, `history.flow_sync`, `history.flow_capture_public_trades`, `history.coverage`.
- Readiness: `data.forecast_input_readiness`, `data.forecast_input_readiness_core`, `data.flow_storage_coverage`, `data.flow_backfill_causal_availability`.
- Contracts and records: `prediction.contract`, `outcome.contract`, `forecast.build`, `forecast.create`, `forecast.get`, `forecast.list`.
- Outcomes and validation: `outcome.evaluate`, `outcome.list`, `validation.walk_forward`, `validation.methods_walk_forward`, `validation.assess_model`, `validation.development_holdout`, `validation.calibrate_model`, `validation.calibrate_core`.
- Analysis and workflows: `analysis.run_core`, `workflow.start_core_validation`, `workflow.status`, `workflow.list`, `workflow.approve`

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

DuckDB is the current HOME backend for the Birzha state and market-history stores. The source also has configurable local/container defaults, including `/tmp` paths; those defaults are not evidence of durable HOME configuration. HOME acceptance requires separate installation and runtime verification.

YDB-related adapters remain in the repository for compatibility; DuckDB is the current HOME state/history backend.

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
