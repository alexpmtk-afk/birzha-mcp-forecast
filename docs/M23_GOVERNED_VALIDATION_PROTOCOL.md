# M23 governed validation protocol

Protocol ID: `M23_HISTORICAL_GOVERNED_V1`

## Purpose

Provide one pre-declared historical test of the shared six-market forecast configuration without tuning dates, data or acceptance thresholds after seeing model performance.

Markets: `SBER`, `Si`, `BR`, `GOLD`, `IMOEX`, `RTSI`.

Horizons: 5, 10, 20 exchange sessions.

## Frozen periods

- Development: `2021-01-01..2022-12-31`.
- Holdout: `2023-01-01..2024-12-31`.
- Forecast T0 spacing: 5 exchange sessions.
- Maximum raw forecast points: 80.

These dates were selected before M23 performance is computed. They may be enlarged only when real exchange-session/data availability makes the declared sample impossible, and only before performance is inspected. They must not be moved because of model quality.

## Why 2025-2026 is excluded

The old SBER/Si model-assessment runner used `2025-01-01..2026-05-31`. GitHub Actions evidence from 2026-08-30 shows the statistical assessment actually ran and uploaded evidence. That window is therefore treated as previously consumed and is not the governed M23 holdout.

## Frozen acceptance thresholds

- Minimum observations per horizon: 20.
- Minimum directional coverage: 0.70.
- 95% Wilson lower bound must be strictly above 0.50.

M23 does not weaken thresholds to improve the chance of PASS.

## Non-overlap rule

Statistics use non-overlapping forecast windows. With T0 every 5 sessions:

- 5-session horizon: every observation;
- 10-session horizon: every second observation;
- 20-session horizon: every fourth observation.

Raw counts and thinning stride remain visible in evidence.

## Two-stage holdout governance

1. D1 session calendars first prove that both periods can supply the required sample on all six markets.
2. Full D1/H1/M15 history and optional flow are prepared before candidate comparison.
3. The normal validation run evaluates development only. The holdout remains sealed even if development passes.
4. A successful development-only run emits two independent SHA-256 identities:
   - `selected_model_fingerprint` — Forecast Engine version, selected parameters, protocol and development identity;
   - `selected_data_fingerprint` — exact prepared contract calendar, D1/H1/M15 rows and prepared TradeStats/FUTOI rows used by governed validation.
5. The final holdout run is a separate explicit command using `--open-holdout` and must supply both prior fingerprints.
6. The final run does not prepare or repair history. It rechecks readiness and recomputes the dataset fingerprint from the frozen YDB rows.
7. `MODEL_FINGERPRINT_MISMATCH` or `DATA_FINGERPRINT_MISMATCH` stops before the holdout claim and before holdout performance is read.
8. Immediately before the first holdout performance read, YDB atomically stores a durable claim containing the holdout range, protocol, Forecast Engine version, model fingerprint, data fingerprint and UTC timestamp.
9. The claim is written before performance is read. A crash after claiming still consumes the holdout rather than permitting another look.
10. The same or any overlapping holdout period is blocked on later runs as `HOLDOUT_ALREADY_CONSUMED`.
11. Changing model parameters, engine code, historical rows, protocol text, computer, chat or execution channel does not make a consumed holdout fresh again.
12. A poor holdout result means rejection; the same holdout must not be reused for tuning and re-tested as if independent.

Checking session capacity and computing a cryptographic dataset identity before the claim are allowed because neither step computes or exposes holdout model performance.

## Data governance

Validation uses `FROZEN_PREPARED_YDB`. Candidate comparison and final holdout evaluation must not download missing prices or optional flow, change rolling-contract identity, or repair the dataset while models are being compared.

The dataset fingerprint is content-sensitive. A changed candle payload, contract-session mapping, TradeStats row or FUTOI row changes the fingerprint and prevents the final holdout run from proceeding under the old seal.

All preparation requests stay behind the global request controller and distributed YDB pacing gate.

## Result semantics

- `DEVELOPMENT_ACCEPTED_HOLDOUT_SEALED` — development passed; final period is still untouched and both fingerprints are available for a later explicit final run.
- `ACCEPTED` — frozen model passed the governed holdout evaluation.
- `REJECTED` — evaluation completed and statistical criteria failed.
- `INSUFFICIENT_DATA` / `INSUFFICIENT_SAMPLE` — not enough independent evidence.
- `MODEL_FINGERPRINT_MISMATCH` — selected model no longer matches the sealed development identity; holdout remains sealed.
- `DATA_FINGERPRINT_MISMATCH` — prepared validation dataset changed after development; holdout remains sealed.
- `HOLDOUT_ALREADY_CONSUMED` — the requested final period has already been spent and cannot be evaluated again.
- `DATA_NOT_READY` — mandatory historical data is not proven complete.

## Promotion rule

`ACCEPTED` is permitted only after real tests/compile checks, M22 GOLD/GD* evidence, full mandatory data readiness, successful sealed development, matching model and dataset fingerprints, durable one-shot holdout claiming, and one governed holdout evaluation. `REJECTED` and `INSUFFICIENT_DATA` remain valid outcomes and must not be converted to PASS by changing criteria after the result is known.
