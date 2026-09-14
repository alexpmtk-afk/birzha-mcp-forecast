# M23 governed validation protocol

Protocol ID: `M23_HISTORICAL_GOVERNED_V1`

## Purpose

Provide one pre-declared historical test of the shared six-market forecast configuration without tuning dates, data or acceptance thresholds after seeing model performance.

Markets: `SBER`, `Si`, `BR`, `GOLD`, `IMOEX`, `RTSI`.

Horizons: 5, 10, 20 exchange sessions.

## Frozen periods

- Development: `2021-01-01..2022-12-31`.
- Holdout: `2023-01-01..2024-12-31`.
- Nominal raw T0 cadence: every 5 exchange sessions.
- Maximum raw forecast points: 80 unless a pre-performance capacity-only enlargement explicitly requires a higher scan ceiling.

These dates were selected before M23 performance is computed. They may be enlarged only when real exchange-session/data availability makes the declared sample impossible, and only before performance is inspected. They must not be moved because of model quality.

A capacity-only enlargement is not a model evaluation. It may inspect the verified session/contract calendar and exact-contract maturity only. It must not read forecasts, outcomes, directional accuracy, coverage, Wilson bounds or any other performance statistic.

## Current BR capacity evidence

The exact-contract BR rule is currently the only known sample-capacity blocker. A capacity-only run over `2013-01-01..2024-12-31` synchronized 145 BR contracts and 3026 D1 candles without evaluating the model. With the frozen minimum of 20 independent observations:

- Development candidate `2013-01-01..2018-12-31`: 14/14/14 observations for 5/10/20 sessions — insufficient.
- Holdout candidate `2019-01-01..2024-12-31`: 18/18/18 observations for 5/10/20 sessions — insufficient.

Therefore those candidate periods are not frozen as the final BR enlargement. Repeating the same period cannot change the conclusion. Further date planning must remain capacity-only and must preserve the exact-contract rule unless that rule is changed by a separate explicit architecture/protocol decision.

## Why 2025-2026 is excluded

The old SBER/Si model-assessment runner used `2025-01-01..2026-05-31`. GitHub Actions evidence from 2026-08-30 shows the statistical assessment actually ran and uploaded evidence. That window is therefore treated as previously consumed and is not the governed M23 holdout.

## Frozen acceptance thresholds

- Minimum observations per horizon: 20.
- Minimum directional coverage: 0.70.
- 95% Wilson lower bound must be strictly above 0.50.

M23 does not weaken thresholds to improve the chance of PASS.

## Non-overlap rule

Statistics use genuinely non-overlapping forecast windows selected from the **actual retained T0 candidates**.

- The nominal dense T0 cadence is every 5 exchange sessions.
- A 5/10/20-session horizon therefore has nominal stride evidence of 1/2/4 raw T0 positions respectively.
- Futures candidates that fail exact-contract maturity, data-readiness or other causal checks are removed before non-overlap selection.
- The remaining real T0/target intervals are then selected greedily in chronological order: a candidate is retained only when its T0 is not earlier than the prior retained target boundary for that horizon.
- A rejected/skipped candidate already creates spacing and must **not** be thinned a second time by blindly taking every second/fourth retained row.

Evidence must preserve raw candidate counts, actual retained T0/target timestamps and the resulting independent counts. The nominal 1/2/4 stride may be reported as reference evidence but is not reapplied mechanically after filtering.

## Two-stage holdout governance

1. The durable D1 session/contract calendar first proves that both periods can supply the required sample on all six markets.
2. Persistent Data Foundation for validation is D1-only. H1/M15 are contextual on-demand inputs and are not required to exist in the shared historical candle database.
3. The normal validation run evaluates development only. The holdout remains sealed even if development passes.
4. A successful development-only run emits two independent SHA-256 identities:
   - `selected_model_fingerprint` — Forecast Engine version, selected parameters, protocol and development identity;
   - `selected_data_fingerprint` — exact durable D1 contract/session calendar, D1 rows, prepared optional TradeStats/FUTOI rows and the verification ranges that determine whether optional-flow rows are usable. H1/M15 are intentionally excluded because they are on-demand, non-persistent contextual inputs.
5. The final holdout run is a separate explicit command using `--open-holdout` and must supply both prior fingerprints.
6. The final run does not prepare or repair persistent history. It rechecks readiness and recomputes the durable dataset fingerprint from the frozen YDB state.
7. `MODEL_FINGERPRINT_MISMATCH` or `DATA_FINGERPRINT_MISMATCH` stops before the holdout claim and before holdout performance is read.
8. Immediately before the first holdout performance read, YDB atomically stores a durable claim containing the holdout range, protocol, Forecast Engine version, model fingerprint, data fingerprint and UTC timestamp.
9. The claim is written before performance is read. A crash after claiming still consumes the holdout rather than permitting another look.
10. The same or any overlapping holdout period is blocked on later runs as `HOLDOUT_ALREADY_CONSUMED`.
11. Changing model parameters, engine code, historical rows, flow verification state, protocol text, computer, chat or execution channel does not make a consumed holdout fresh again.
12. A poor holdout result means rejection; the same holdout must not be reused for tuning and re-tested as if independent.

Checking session capacity and computing a cryptographic durable dataset identity before the claim are allowed because neither step computes or exposes holdout model performance.

## Data governance

Validation uses frozen persistent D1 YDB state plus causal H1/M15 on-demand context. Candidate comparison and final holdout evaluation must not repair persistent D1 history, change rolling-contract identity or mutate optional-flow verification state while models are being compared.

The durable dataset fingerprint is content- and state-sensitive. A changed D1 candle payload, contract-session mapping, TradeStats row, FUTOI row, or the verification range controlling whether optional-flow data are visible to the read-only validator changes the fingerprint and prevents the final holdout run from proceeding under the old seal. H1/M15 do not enter this durable fingerprint because current architecture explicitly does not persist them.

All preparation requests stay behind the global request controller and distributed YDB pacing gate.

## Capacity enlargement planner

Date enlargement must be mechanical rather than hand-picked. The approved planner policy is:

- read only an already-verified stored D1 session/contract map;
- keep the holdout end fixed at `2024-12-31`;
- evaluate equal-duration, contiguous full-calendar-year development and holdout windows, shortest first;
- keep the minimum observation threshold at 20 and the exact-contract maturity rule unchanged;
- return the first feasible pair or `INSUFFICIENT_STORED_RANGE_OR_CAPACITY`;
- never download prices, compute model outputs or read performance metrics as part of planning.

This planner is a capacity tool only. A feasible result permits a formal period-freeze decision; it does not itself freeze or consume a holdout.

## Result semantics

- `DEVELOPMENT_ACCEPTED_HOLDOUT_SEALED` — development passed; final period is still untouched and both fingerprints are available for a later explicit final run.
- `ACCEPTED` — frozen model passed the governed holdout evaluation.
- `REJECTED` — evaluation completed and statistical criteria failed.
- `INSUFFICIENT_DATA` / `INSUFFICIENT_SAMPLE` — not enough independent evidence.
- `MODEL_FINGERPRINT_MISMATCH` — selected model no longer matches the sealed development identity; holdout remains sealed.
- `DATA_FINGERPRINT_MISMATCH` — prepared validation dataset changed after development; holdout remains sealed.
- `HOLDOUT_ALREADY_CONSUMED` — the requested final period has already been spent and cannot be evaluated again.
- `DATA_NOT_READY` — mandatory persistent historical data is not proven complete.

## Promotion rule

`ACCEPTED` is permitted only after real tests/compile checks, M22 GOLD/GD* evidence, full mandatory persistent D1 readiness, successful sealed development, matching model and dataset fingerprints, durable one-shot holdout claiming, and one governed holdout evaluation. `REJECTED` and `INSUFFICIENT_DATA` remain valid outcomes and must not be converted to PASS by changing criteria after the result is known.
