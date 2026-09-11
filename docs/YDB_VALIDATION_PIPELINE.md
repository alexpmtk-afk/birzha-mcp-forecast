# YDB six-market validation pipeline

This document describes the operator path introduced by M23. It is intentionally separate from legacy GitHub-hosted model smoke checks.

## Goal

Prepare and validate the core research universe using durable YDB history without weakening causal or statistical gates.

Core symbols:

`SBER`, `Si`, `BR`, `GOLD`, `IMOEX`, `RTSI`

Required price timeframes:

`D1`, `H1`, `M15`

Configured legacy validation window:

- development starts `2025-01-01`;
- development ends at `2025-10-01`;
- holdout starts after the split date;
- holdout ends `2026-05-31`.

Important: after correcting overlapping forecast windows, this legacy window is now known to be statistically too short for the unchanged requirement of 20 independent observations at the 20-session horizon. The one-command pipeline therefore returns `INSUFFICIENT_DATA` before any expensive data preparation when the configured dates cannot possibly support the required sample.

The readiness layer includes causal lookback before the chosen validation start:

- D1: 300 calendar days;
- H1: 90 calendar days;
- M15: 30 calendar days.

## One-command operator path

Run from the repository environment that already has authorized `yc` CLI access:

```text
python scripts/run_authorized_ydb_model_pipeline.py --connection-string <YDB_CONNECTION_STRING>
```

The pipeline is fail-closed:

1. Before touching YDB/MOEX, a calendar-day upper-bound check asks whether development and holdout could even theoretically contain the unchanged minimum of 20 non-overlapping observations per horizon. If not, the pipeline returns `INSUFFICIENT_DATA`, `prepare=NOT_RUN`, `validation=NOT_RUN`, and `holdout_evaluated=false`.
2. If the dates are theoretically sufficient, `run_authorized_ydb_prepare_validation.py` repairs/validates required price history and prepares optional historical flow.
3. Data readiness must report all 18 mandatory price requirements as `READY`.
4. A second exact capacity check uses the verified stored exchange-session calendars. If development still cannot supply the required independent sample, model calibration is skipped and holdout remains untouched.
5. Only then `run_authorized_ydb_validation.py` runs six-market development calibration.
6. The validation phase uses `FROZEN_PREPARED_YDB`: price history is read from prepared YDB without another preparation pass; optional TradeStats/FUTOI is read only from the current verified `FLOW_V1` cache and is otherwise treated as missing/`DEGRADED` rather than fetched during candidate comparison.
7. Historical instrument identity is store-backed in frozen validation. Direct instruments are recovered from stored metadata; `Si/BR/GOLD` T0 contracts are recovered from the versioned D1 session calendar (`trade_date -> exact SECID -> stored Instrument`). Missing or ambiguous identity fails closed instead of falling back to live MOEX resolution. Outcome evaluation prefers the same stored exact-contract identity.
8. Walk-forward counts only snapshots whose mandatory D1/H1/M15 inputs pass minimum-history quality. Optional flow may be degraded without invalidating otherwise complete price data.
9. Forecast windows used for statistics do not overlap. With T0 every 5 sessions, the 5-session horizon uses every observation, the 10-session horizon every second observation, and the 20-session horizon every fourth observation. Reports preserve both raw observation count and the sampling stride.
10. Calibration is acceptance-first: a development-accepted candidate outranks a development-rejected candidate even if the rejected candidate has a higher average numeric score.
11. Holdout is not opened until the selected configuration is accepted on every required development market. If development is rejected, insufficient, or failed, `holdout_evaluated=false` and no holdout metrics are produced.
12. `REJECTED` and `INSUFFICIENT_DATA` are valid model results, not software failures and must never be relabelled as PASS.

## Statistical sample rule

The acceptance threshold remains unchanged at 20 observations per horizon. M23 no longer counts overlapping 10- or 20-session forecast windows as independent samples.

The previous 24–60 point execution limits were therefore insufficient for a 20-session horizon when T0 advances every 5 sessions. Authorized validation now defaults to `max_points=80`, and the core validator allows up to 240 points. This changes only how many forecasts may be evaluated; it does not weaken acceptance thresholds.

For the legacy development window `2025-01-01..2025-10-01`, even the impossible best case in which every calendar day were an exchange session would provide at most 13 independent 20-session observations. Real exchange capacity is lower. A longer governed development/holdout design is therefore required before final model acceptance can be attempted with the current thresholds.

## Evidence artifacts

The pipeline writes up to three JSON artifacts:

- `artifacts/ydb_validation_data_preparation.json` — preparation operations, 18-item readiness evidence, optional-flow failures and zero-row warnings;
- `artifacts/ydb_model_validation.json` — statistical capacity, selected parameters when evaluation occurs, development/holdout reports, `data_mode=FROZEN_PREPARED_YDB`, and `model_status`;
- `artifacts/ydb_model_pipeline.json` — top-level execution summary, including early `INSUFFICIENT_DATA` results when the configured periods are mathematically too short.

Important model statuses include:

- `ACCEPTED`;
- `REJECTED`;
- `INSUFFICIENT_DATA` / `INSUFFICIENT_SAMPLE`;
- `NOT_EVALUATED` when mandatory data is not ready.

## Current verification semantics

M23 does not trust price readiness markers created by older semantics in the production-like path:

- D1 uses `D1_SESSION_V1`: the current resolver/contract chain is audited against official exchange sessions; one completed daily candle per expected active session is required.
- H1 uses `H1_FULL_V1`: every unverified exchange session is fetched from the provider as a complete bounded response before certification.
- M15 uses `M15_FULL_V1`: every unverified exchange session is re-created from bounded M1 retrieval before certification.
- H1/M15 certification is incremental. Successful chunks are durable, so retries and future range extensions fetch only sessions not already proven by the current version.
- Full intraday verification is based on the current provider response, not merely on an older row remaining in storage.
- Rolling futures use `ROLLING_HISTORY_V2_PREWARM` composed with the timeframe generation.
- The D1 logical-root session calendar uses the same composed version key. Legacy raw `GOLD` calendar rows remain archival but are not visible to current validation.
- When a later quarterly futures contract becomes active, its exact-contract history is preloaded before rollover (D1 300 days, H1 90 days, M15 30 days, limited by actual exchange availability).
- Exact-contract warmup uses `CONTRACT_WARMUP_V1`; a missing expected preroll session fails closed.

Historical analytical flow is versioned with `FLOW_V1`. Legacy TradeStats/FUTOI verification markers are not sufficient after M23, which is particularly important for corrected `GOLD -> GD` FUTOI routing.

## Safety rules

- All MOEX/ALGOPACK requests during preparation remain behind `ProcessUpstreamControlPlane`.
- Remote preparation uses distributed `YdbSlotPacingGate`; limits are never bypassed for backfill speed.
- Model validation does not repair or extend prepared price/flow datasets while candidates are being compared.
- Frozen validation does not silently use live MOEX instrument resolution when prepared identity is missing.
- A resolver returning no historical segments fails closed.
- A rolling-futures segment with zero stored candles fails closed.
- Unsupported index TradeStats is optional degraded context and must not block mandatory price data.
- Development and holdout do not share the split date.
- Holdout is not evaluated when development has not passed.
- Acceptance thresholds are not relaxed to force a PASS.

## Promotion gate

Do not merge/promote M23 until all of the following are true:

- full real `pytest` passes;
- `compileall` passes;
- M22 GOLD routing is proven against real MOEX/YDB data;
- a statistically sufficient governed development/holdout period is selected, or a different overlap-aware statistical method is explicitly approved;
- validation preparation finishes with mandatory price readiness `READY` for that approved period;
- optional flow warnings/errors are reviewed rather than silently ignored;
- the six-market validation artifact is produced and reviewed;
- execution-channel failures are reported precisely and are never collapsed into a generic `Windows offline` status.
