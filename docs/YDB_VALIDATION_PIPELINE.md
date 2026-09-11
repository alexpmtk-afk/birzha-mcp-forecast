# YDB six-market validation pipeline

This document describes the M23 operator path for honest six-market historical validation.

## Goal

Validate the core universe `SBER`, `Si`, `BR`, `GOLD`, `IMOEX`, `RTSI` on durable YDB history without weakening causal or statistical gates.

Required price timeframes: `D1`, `H1`, `M15`.

Governed protocol: `M23_HISTORICAL_GOVERNED_V1`.

Default periods:

- development: `2021-01-01..2022-12-31`;
- holdout: `2023-01-01..2024-12-31`;
- T0 step: 5 exchange sessions;
- maximum raw forecast points: 80;
- minimum accepted observations per horizon: 20.

The old `2025-01-01..2026-05-31` SBER/Si window is not a fresh holdout. The legacy GitHub model-assessment workflow actually ran successfully on 2026-08-30, including its statistical assessment step and uploaded evidence. M23 therefore excludes that window from the governed final protocol.

The periods above may be enlarged only if exchange-session/data availability proves them insufficient, and only before model-performance results are inspected. Dates must never be moved because a model result looks poor.

The readiness layer adds causal lookback before development start:

- D1: 300 calendar days;
- H1: 90 calendar days;
- M15: 30 calendar days.

## One-command operator path

```text
python scripts/run_authorized_ydb_model_pipeline.py --connection-string <YDB_CONNECTION_STRING>
```

The pipeline is fail-closed and deliberately staged:

1. A calendar-day upper-bound check rejects dates that cannot possibly supply the unchanged minimum sample.
2. Preparation fetches/verifies only D1 first for all six markets. This creates the versioned real exchange-session calendars at low cost.
3. Real session capacity is checked for both development and holdout on every market. If either period is too short, the result is `INSUFFICIENT_DATA`; H1/M15/flow are not backfilled and holdout is not evaluated.
4. Only when both periods are viable are H1, M15 and optional historical flow prepared.
5. All 18 mandatory price requirements must be `READY`.
6. Validation re-checks exact stored-session capacity for development and holdout before calibration.
7. Candidate comparison uses `FROZEN_PREPARED_YDB`; it cannot repair data or silently fetch new price/flow data between candidates.
8. Rolling-futures identity at T0 is recovered from the versioned stored session calendar. Missing or ambiguous identity fails closed instead of falling back to live MOEX.
9. Only snapshots with complete mandatory D1/H1/M15 history count. Optional flow may be degraded.
10. Statistical windows do not overlap. With T0 every 5 sessions, 5-session results use every observation, 10-session results every second, and 20-session results every fourth. Reports preserve raw count and sampling stride.
11. Candidate selection is development-only and acceptance-first. A rejected candidate cannot beat an accepted candidate merely through a better average score.
12. Holdout stays sealed until the selected configuration passes development. If development is rejected, failed, or insufficient, `holdout_evaluated=false`.
13. `REJECTED` and `INSUFFICIENT_DATA` are valid scientific results, not software failures.

## Statistical gates

M23 does not change the frozen acceptance thresholds:

- minimum observations: 20 per horizon;
- minimum directional coverage: 0.70;
- 95% Wilson lower bound must be strictly above 0.50.

The old 24–60 forecast-point limits were incompatible with 20 independent observations at the 20-session horizon when T0 advances every 5 sessions. Authorized validation therefore allows 80 raw points by default; this increases sample capacity but does not weaken acceptance criteria.

## Evidence artifacts

The pipeline writes up to three JSON artifacts:

- `artifacts/ydb_validation_data_preparation.json` — D1 calendar phase, exact session capacity, price/flow preparation, readiness and warnings;
- `artifacts/ydb_model_validation.json` — protocol identity, exact development/holdout capacity, selected parameters when evaluated, calibration/holdout results, and `model_status`;
- `artifacts/ydb_model_pipeline.json` — top-level execution summary.

Important statuses:

- `ACCEPTED`;
- `REJECTED`;
- `INSUFFICIENT_DATA` / `INSUFFICIENT_SAMPLE`;
- `NOT_EVALUATED` when mandatory data is not ready.

## Data verification semantics

- D1 uses `D1_SESSION_V1` against official exchange sessions.
- H1 uses `H1_FULL_V1` and M15 uses `M15_FULL_V1`; current provider responses, not stale rows, prove completeness.
- H1/M15 repair is incremental and resumable in bounded session chunks.
- Rolling roots `Si/BR/GOLD` additionally use `ROLLING_HISTORY_V2_PREWARM`.
- The rolling D1 session calendar uses the same versioned identity, so legacy raw GOLD session rows are not accepted.
- A later futures contract receives exact-contract causal pre-roll history before it becomes active: D1 300 days, H1 90 days, M15 30 days, limited by actual availability.
- Exact-contract warmup uses `CONTRACT_WARMUP_V1`; a missing expected pre-roll session fails closed.
- Historical analytical flow uses `FLOW_V1`. The corrected GOLD FUTOI transport is `GD`.

## Legacy workflow

`.github/workflows/model-acceptance.yml` is now manual-only and named `Legacy SBER-Si Smoke - NOT Model Acceptance`. Its artifact and runner script explicitly state that it is legacy smoke evidence and must not be used as six-market model-acceptance evidence.

## Safety

- All MOEX/ALGOPACK requests remain behind `ProcessUpstreamControlPlane`.
- Remote preparation requires distributed `YdbSlotPacingGate`.
- Request limits are never bypassed for backfill speed.
- Store-first verification and per-session markers make long retries resumable.
- Empty resolver/contract segments and incomplete expected sessions fail closed.
- Development and holdout never share the split date.
- Holdout is never opened merely to see whether a rejected development model would have worked.
- Acceptance thresholds are never relaxed to force PASS.

## Promotion gate

Do not merge/promote M23 until all are true:

- full real `pytest` passes;
- `compileall` passes;
- M22 GOLD routing is proven against real MOEX/YDB data;
- D1-first capacity confirms both governed periods are statistically viable on all six markets;
- all 18 mandatory price requirements are READY;
- optional flow warnings/errors are reviewed;
- six-market development is computed from frozen prepared data;
- holdout is opened only if development passes;
- final evidence is reviewed without relabelling rejection as software failure;
- execution-channel failures are reported precisely and never as a generic `Windows offline` status.
