# YDB six-market validation pipeline

This document describes the M23 operator path for honest six-market historical validation.

## Goal

Validate `SBER`, `Si`, `BR`, `GOLD`, `IMOEX`, `RTSI` on durable YDB history without weakening causal or statistical gates.

Required price timeframes: `D1`, `H1`, `M15`.

Governed protocol: `M23_HISTORICAL_GOVERNED_V1`.

Default periods:

- development: `2021-01-01..2022-12-31`;
- holdout: `2023-01-01..2024-12-31`;
- T0 step: 5 exchange sessions;
- maximum raw forecast points: 80;
- minimum accepted observations per horizon: 20.

The old `2025-01-01..2026-05-31` SBER/Si window is not a fresh holdout because the legacy statistical assessment actually ran. M23 excludes it from the governed final protocol.

The readiness layer adds causal lookback before development start:

- D1: 300 calendar days;
- H1: 90 calendar days;
- M15: 30 calendar days.

## Stage 1 — prepare and evaluate development only

Run:

```text
python scripts/run_authorized_ydb_model_pipeline.py --connection-string <YDB_CONNECTION_STRING>
```

This stage may prepare missing history through the controlled MOEX path. If all six development markets pass, the result is:

`DEVELOPMENT_ACCEPTED_HOLDOUT_SEALED`

The artifact contains both values needed for the final run:

- `selected_model_fingerprint`;
- `selected_data_fingerprint`.

The holdout is not evaluated and no durable holdout claim is written at this stage.

## Stage 2 — explicit one-shot holdout opening

Only after Stage 1 evidence, tests and data have been reviewed, run:

```text
python scripts/run_authorized_ydb_model_pipeline.py \
  --connection-string <YDB_CONNECTION_STRING> \
  --open-holdout \
  --expected-model-fingerprint <SELECTED_MODEL_FINGERPRINT> \
  --expected-data-fingerprint <SELECTED_DATA_FINGERPRINT>
```

The final run deliberately skips preparation. It must use the already frozen YDB dataset.

Before holdout performance is read, the final run:

1. rechecks all mandatory readiness markers;
2. rechecks exact development/holdout session capacity;
3. recomputes the cryptographic dataset fingerprint from the stored contract calendar, D1/H1/M15 rows and prepared TradeStats/FUTOI rows;
4. reruns development selection on frozen data and recomputes the selected-model fingerprint;
5. requires both fingerprints to match the sealed Stage 1 values;
6. only then atomically writes the one-shot YDB holdout claim;
7. only after the claim is durable does it read holdout performance.

A data or model mismatch stops before the claim and keeps the holdout sealed.

## Pipeline guarantees

1. Calendar-day upper-bound checks reject impossible statistical periods early.
2. D1 is prepared first to establish real exchange-session calendars cheaply.
3. Contract-aware session capacity is checked on both development and holdout before expensive intraday backfill.
4. H1/M15 are fetched only after both periods are statistically viable.
5. Long MOEX work remains behind `ProcessUpstreamControlPlane` and distributed `YdbSlotPacingGate`.
6. H1/M15 verification is durable per bounded session chunk and resumes after interruption.
7. All 18 mandatory price requirements must be `READY`.
8. Validation uses `FROZEN_PREPARED_YDB`; it cannot repair data while comparing candidates.
9. Rolling-futures identity at T0 comes from the versioned stored session calendar. Missing or ambiguous identity fails closed.
10. Only complete D1/H1/M15 snapshots count. Optional flow may degrade but does not silently mutate during validation.
11. Statistical windows are non-overlapping: with T0 every 5 sessions, 5-session results use every observation, 10-session results every second, 20-session results every fourth.
12. Candidate selection is development-only and acceptance-first.
13. Rejected, failed or insufficient development never opens holdout.
14. Final holdout opening requires matching model and data fingerprints.
15. The YDB claim records holdout dates, protocol, Forecast Engine version, model fingerprint, data fingerprint and timestamp.
16. Same or overlapping holdout ranges cannot be claimed again.
17. A crash after claim still consumes the holdout; it is not silently made fresh again.

## Statistical gates

M23 keeps the frozen thresholds:

- minimum observations: 20 per horizon;
- minimum directional coverage: 0.70;
- 95% Wilson lower bound strictly above 0.50.

80 raw forecast points are allowed because non-overlap thinning requires enough candidates to reach 20 independent 20-session observations. This increases sample capacity; it does not weaken the acceptance thresholds.

## Evidence artifacts

- `artifacts/ydb_validation_data_preparation.json` — D1 calendar phase, exact session capacity, price/flow preparation, readiness and warnings.
- `artifacts/ydb_model_validation.json` — protocol identity, capacities, development calibration, model/data fingerprints, holdout state and final claim/evaluation when explicitly opened.
- `artifacts/ydb_model_pipeline.json` — top-level operator summary.

Important statuses:

- `DEVELOPMENT_ACCEPTED_HOLDOUT_SEALED`;
- `ACCEPTED`;
- `REJECTED`;
- `INSUFFICIENT_DATA` / `INSUFFICIENT_SAMPLE`;
- `MODEL_FINGERPRINT_MISMATCH`;
- `DATA_FINGERPRINT_MISMATCH`;
- `HOLDOUT_ALREADY_CONSUMED`;
- `DATA_NOT_READY`.

## Data verification semantics

- D1 uses `D1_SESSION_V1` against official exchange sessions.
- H1 uses `H1_FULL_V1` and M15 uses `M15_FULL_V1`.
- H1/M15 repair is incremental and resumable in bounded session chunks.
- Rolling roots `Si/BR/GOLD` use `ROLLING_HISTORY_V2_PREWARM`.
- The rolling D1 session calendar uses the same versioned identity, so legacy raw `GOLD` rows are not accepted.
- Later futures contracts receive exact-contract pre-roll causal history: D1 300 days, H1 90 days, M15 30 days, limited by real availability.
- Exact-contract warmup uses `CONTRACT_WARMUP_V1`; missing expected pre-roll sessions fail closed.
- Historical analytical flow uses `FLOW_V1`; corrected GOLD FUTOI transport is `GD`.
- The dataset SHA-256 is content-sensitive to the stored contract-session map, exact price rows and prepared flow rows.

## Legacy workflow

`.github/workflows/model-acceptance.yml` is manual-only and named `Legacy SBER-Si Smoke - NOT Model Acceptance`. It must not be used as six-market acceptance evidence.

## Promotion gate

Do not merge/promote M23 until all are true:

- full real `pytest` passes;
- `compileall` passes;
- M22 GOLD routing is proven against real MOEX/YDB data;
- D1-first capacity proves both governed periods statistically viable on all six markets;
- all 18 mandatory price requirements are READY;
- optional flow warnings/errors are reviewed;
- six-market development is computed from frozen prepared data;
- model and dataset fingerprints are captured from the sealed development result;
- final holdout is opened only by an explicit second-stage command using both matching fingerprints;
- durable one-shot claim is proven against real YDB;
- final evidence is reviewed without relabelling rejection as software failure;
- execution-channel failures are reported precisely and never as a generic `Windows offline` status.
