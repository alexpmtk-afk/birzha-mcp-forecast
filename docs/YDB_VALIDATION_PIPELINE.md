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
4. A statistical `REJECTED` result is a valid computed model result, not a software failure and must not be relabelled as PASS.

## Evidence artifacts

The pipeline writes three JSON artifacts:

- `artifacts/ydb_validation_data_preparation.json` — preparation operations and 18-item readiness evidence;
- `artifacts/ydb_model_validation.json` — selected parameters, development/holdout reports and `model_status`;
- `artifacts/ydb_model_pipeline.json` — compact top-level execution summary.

Important top-level statuses:

- preparation: `READY` / `READY_WITH_OPTIONAL_FLOW_ERRORS` / `DATA_NOT_READY`;
- validation run: `COMPUTED` only after readiness succeeds;
- model: `ACCEPTED`, `REJECTED`, or an insufficient/failed statistical state from the unchanged acceptance layer.

## Safety rules

- All MOEX/ALGOPACK requests remain behind `ProcessUpstreamControlPlane`.
- Remote execution uses the distributed `YdbSlotPacingGate`; limits are never bypassed for backfill speed.
- M15 `M15_FULL_V1` verification cannot reuse old date-only M15 markers; the first M23 pass re-fetches every expected session in bounded chunks.
- Rolling futures `Si`, `BR`, `GOLD` do not trust old root-level verification markers.
- GOLD uses quarterly `GD*` contracts for historical price/flow routing and `GD` for FUTOI transport.
- Unsupported index TradeStats is optional degraded context and must not block the mandatory price path.
- Development and holdout do not share the split date.
- Acceptance thresholds are not relaxed to force a PASS.

## Promotion gate

Do not merge/promote M23 until all of the following are true:

- full local `pytest` passes;
- `compileall` passes;
- M22 GOLD routing is proven against real MOEX/YDB data;
- validation preparation finishes with mandatory price readiness `READY`;
- the six-market validation artifact is produced and reviewed;
- the temporary/legacy runner situation is not mistaken for code failure.
