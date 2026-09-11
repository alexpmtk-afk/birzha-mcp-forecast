# M23 governed validation protocol

Protocol ID: `M23_HISTORICAL_GOVERNED_V1`

## Purpose

Provide one pre-declared historical test of the shared six-market forecast configuration without tuning dates or acceptance thresholds after seeing model performance.

Markets: `SBER`, `Si`, `BR`, `GOLD`, `IMOEX`, `RTSI`.

Horizons: 5, 10, 20 exchange sessions.

## Frozen periods

- Development: `2021-01-01..2022-12-31`.
- Holdout: `2023-01-01..2024-12-31`.
- Forecast T0 spacing: 5 exchange sessions.
- Maximum raw forecast points: 80.

These dates were selected before M23 performance is computed. They may be enlarged only when real exchange-session/data availability makes the declared sample impossible, and only before performance is inspected. They must not be moved because of model quality.

## Why 2025-2026 is excluded

The old SBER/Si model-assessment runner used `2025-01-01..2026-05-31`. GitHub Actions evidence from 2026-08-30 shows the `assess` job completed successfully, including `Run real statistical model assessment` and artifact upload. That window is therefore treated as previously consumed for model evaluation and is not used as the governed M23 holdout.

## Frozen acceptance thresholds

- Minimum observations per horizon: 20.
- Minimum directional coverage: 0.70.
- 95% Wilson lower bound must be strictly above 0.50.

M23 does not adopt new thresholds merely to improve the chance of PASS.

## Non-overlap rule

Statistics use non-overlapping forecast windows. With T0 every 5 sessions:

- 5-session horizon: every observation;
- 10-session horizon: every second observation;
- 20-session horizon: every fourth observation.

Raw counts and thinning stride remain visible in evidence.

## Holdout governance

1. D1 session calendars first prove that both periods can supply the required sample on all six markets.
2. Full price history is prepared and verified before candidate comparison.
3. Development selects the shared configuration.
4. If development does not pass, holdout is not evaluated.
5. Immediately before the first holdout read, YDB stores a durable claim containing the holdout range, protocol ID, selected-model fingerprint and UTC timestamp.
6. The claim is written before performance is read. If the process crashes after the claim, the holdout remains consumed rather than becoming eligible for another look.
7. The same or any overlapping holdout period is blocked on later runs as `HOLDOUT_ALREADY_CONSUMED`.
8. Changing model parameters, protocol text, computer, chat or execution channel does not make a consumed holdout fresh again.
9. A poor holdout result means rejection; the same holdout must not be reused for tuning and re-tested as if independent.

Checking session capacity before the claim is allowed because that step does not inspect returns or model performance.

## Data governance

Validation uses `FROZEN_PREPARED_YDB`. Candidate comparison must not download missing prices or optional flow, change rolling-contract identity, or repair the dataset while models are being compared.

All preparation requests stay behind the global request controller and distributed YDB pacing gate.

## Result semantics

- `ACCEPTED` — frozen model passed the governed evaluation.
- `REJECTED` — evaluation completed and statistical criteria failed.
- `INSUFFICIENT_DATA` / `INSUFFICIENT_SAMPLE` — not enough independent evidence.
- `HOLDOUT_ALREADY_CONSUMED` — the requested final period has already been spent and cannot be evaluated again.
- `DATA_NOT_READY` — mandatory historical data is not proven complete.

## Promotion rule

`ACCEPTED` is permitted only after real tests/compile checks, M22 GOLD/GD* evidence, full mandatory data readiness, successful development, durable one-shot holdout claiming, and one governed holdout evaluation. `REJECTED` and `INSUFFICIENT_DATA` remain valid outcomes and must not be converted to PASS by changing criteria after the result is known.
