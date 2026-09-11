# YDB six-market validation pipeline

This document describes the operator path introduced by M23. It is intentionally separate from legacy GitHub-hosted model smoke checks.

## Goal

Prepare and validate the core research universe using durable YDB history without weakening causal or statistical gates.

Core symbols:

`SBER`, `Si`, `BR`, `GOLD`, `IMOEX`, `RTSI`

Required price timeframes:

`D1`, `H1`, `M15`

Validation window defaults:

- development starts `2025-01-01`;
- development ends at `2025-10-01`;
- holdout starts after the split date, from the next eligible session;
- holdout ends `2026-05-31`.

The readiness layer automatically includes causal lookback before the validation start:

- D1: 300 calendar days;
- H1: 90 calendar days;
- M15: 30 calendar days.

## One-command operator path

Run from the repository environment that already has authorized `yc` CLI access:

```text
python scripts/run_authorized_ydb_model_pipeline.py --connection-string <YDB_CONNECTION_STRING>
```

The pipeline is fail-closed:

1. `run_authorized_ydb_prepare_validation.py` repairs/validates required price history and prepares optional historical flow.
2. Data readiness must report all 18 mandatory price requirements as `READY`.
3. Only then `run_authorized_ydb_validation.py` runs six-market development calibration and untouched holdout evaluation.
4. The validation phase uses `FROZEN_PREPARED_YDB`: price history is read from the prepared YDB store without another preparation pass; optional TradeStats/FUTOI is read only when the current `FLOW_V1` cache range is verified and is otherwise treated as missing/`DEGRADED` rather than fetched during candidate comparison.
5. Walk-forward counts only snapshots whose mandatory D1/H1/M15 price timeframes pass their minimum-history quality checks. Optional flow may be degraded without invalidating an otherwise complete price snapshot.
6. Calibration never prefers a development-rejected candidate over a development-accepted candidate merely because its average numeric score is higher. For the shared six-market model, passing all development markets outranks average score; if none pass all markets, the ranking falls back through accepted-market count, worst development status, and then objective score.
7. A statistical `REJECTED` result is a valid computed model result, not a software failure and must not be relabelled as PASS.

## Evidence artifacts

The pipeline writes three JSON artifacts:

- `artifacts/ydb_validation_data_preparation.json` — preparation operations, 18-item readiness evidence, optional-flow failures and zero-row warnings;
- `artifacts/ydb_model_validation.json` — selected parameters, development/holdout reports, explicit `data_mode=FROZEN_PREPARED_YDB`, and `model_status`;
- `artifacts/ydb_model_pipeline.json` — compact top-level execution summary.

Important preparation statuses include:

- `READY`;
- `READY_WITH_OPTIONAL_FLOW_WARNINGS`;
- `READY_WITH_OPTIONAL_FLOW_ERRORS`;
- `DATA_NOT_READY`.

The validation run is `COMPUTED` only after mandatory price readiness succeeds. The model result remains separate: `ACCEPTED`, `REJECTED`, or an insufficient/failed statistical state from the unchanged acceptance layer.

## Current verification semantics

M23 does not trust price readiness markers created by older semantics in the production-like path:

- D1 uses `D1_SESSION_V1`: the current resolver/contract chain is audited against official exchange sessions; one completed daily candle per expected active session is required.
- H1 uses `H1_FULL_V1`: every unverified exchange session is fetched from the provider as a complete bounded response before the session/range is certified.
- M15 uses `M15_FULL_V1`: every unverified exchange session is re-created from bounded M1 retrieval before certification.
- H1/M15 certification is incremental. Successful chunks are durable, so retries and future range extensions fetch only sessions not already proven by the current version.
- Full intraday verification is based on the current provider response, not merely on the fact that an older row for that date remains in storage.
- Rolling futures use `ROLLING_HISTORY_V2_PREWARM` composed with the timeframe generation. Examples: `GOLD#ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V1` and `GOLD#ROLLING_HISTORY_V2_PREWARM#M15_FULL_V1`.
- The D1 logical-root session calendar uses the same composed version key. Legacy raw `GOLD` calendar rows remain archival data but are not visible to current validation.
- When a later quarterly futures contract becomes the active contract, its own exact-contract history is preloaded before the rollover point (D1 300 days, H1 90 days, M15 30 days, limited by actual exchange availability). Those preroll rows support causal feature lookback but are not added to the logical-root active-session calendar.
- Exact-contract warmup has its own `CONTRACT_WARMUP_V1` verification generation so stale partial intraday days are not trusted.
- If the exchange calendar expects an exact-contract preroll session and the current provider response does not contain it, warmup fails closed and the logical-root range is not certified `READY`.

Historical analytical flow is also versioned with `FLOW_V1`. Legacy TradeStats/FUTOI verification markers are not sufficient after M23, which is particularly important for the corrected `GOLD -> GD` FUTOI transport mapping.

## Safety rules

- All MOEX/ALGOPACK requests during preparation remain behind `ProcessUpstreamControlPlane`.
- Remote preparation uses the distributed `YdbSlotPacingGate`; limits are never bypassed for backfill speed.
- Model validation does not repair or extend prepared price/flow datasets while candidates are being compared.
- Intraday full-session repair is bounded to small exchange-session chunks and is resumable.
- A resolver returning no historical segments fails closed and cannot create a verified marker.
- A rolling-futures contract segment that contains zero stored candles fails closed.
- GOLD uses quarterly `GD*` contracts for historical price/flow routing and `GD` for FUTOI transport.
- Unsupported index TradeStats is optional degraded context and must not block the mandatory price path.
- Supported optional feeds that return zero rows are surfaced explicitly in preparation evidence.
- Development and holdout do not share the split date.
- Acceptance thresholds are not relaxed to force a PASS.

## Promotion gate

Do not merge/promote M23 until all of the following are true:

- full local `pytest` passes;
- `compileall` passes;
- M22 GOLD routing is proven against real MOEX/YDB data;
- validation preparation finishes with mandatory price readiness `READY`;
- optional flow warnings/errors are reviewed rather than silently ignored;
- the six-market validation artifact is produced and reviewed;
- the execution-channel limitation is reported precisely and is not collapsed into a generic `Windows offline` status.
