# M1 v2.2 — transition fixtures and research provenance preflight (2026-10-19)

Canonical authority: [Project Map → «Модель 1 — инструкция», v2.2](https://docs.google.com/spreadsheets/d/1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit#gid=2026101801). This is a **technical decision/test ledger**, not a second specification or project queue.

## What is actually implemented

1. `m1_mandatory_chain.py` is a *read-only auditor of asserted evidence envelopes*, not a Market State, Control or Route calculator. It checks a shared D1/H1/M15 chain and **separate** H5/H10/H20 gates. A consistent envelope is labeled `CHAIN_CONSISTENT_UNVERIFIED`, never ACTIVE or a prediction.
2. `m1_research_governance.py` checks **user-supplied, synthetic or separately authorized** TRAIN/DEVELOPMENT session-index plans and receipt metadata. Explicitly rejects FINAL_HOLDOUT input, overlapping boundaries, insufficient 20-session purge/embargo and outcomes not available by the split end. It does not select windows, load quotes, inspect sealed data or fit parameters.
3. The pre-existing `m1_parameter_manifest.py` from Draft PR #265 is still the incomplete-schema fail-closed preflight. No numeric parameter values are supplied or approved by these two changes.

No MCP tool registration, no Forecast/Outcome Journal change, no HOME or Google Sheets edit, no release-mode change.

## Synthetic transition decision cases

All timestamps and source references in these fixtures are deliberately fictitious; `TRUE` from a caller **cannot attest** a historical market fact.

| Case | Fixed context | Expected local diagnostic | What it does *not* prove |
| --- | --- | --- | --- |
| 01 | D1 TREND_UP, H1 BUYERS/RETESTED/CONFIRMED, same M15 level, admissible evidence to T0 | CHAIN_CONSISTENT_UNVERIFIED for H5/H10/H20 | No raw-price or execution certification |
| 02 | Perfect mirror: TREND_DOWN, SELLERS, same-level confirmed retest | Same non-executable state | No predictive short accuracy |
| 03 | H1 BUYERS/INITIAL, no observed completed retest/hold | CONDITION_FALSE for all horizons | No assumption that strength == time |
| 04 | H1 status PENDING | EVIDENCE_UNAVAILABLE | PENDING is not a disguised FALSE market signal |
| 05 | H1/M15 level IDs differ | CONDITION_FALSE | No counting evidence from an unrelated level |
| 06 | Event flag TRUE | CONDITION_FALSE | EVENT is separate from five base states |
| 07 | One timeframe resolves a different futures SECID | CONDITION_FALSE | No continuous contract execution |
| 08 | H10 structural target fails, H5 and H20 remain admissible | H10 CONDITION_FALSE; other two still unverified | Horizon verdicts cannot be copied |
| 09 | H20 route evidence missing | H20 EVIDENCE_UNAVAILABLE, H5/H10 unaffected | Missing is not zero |
| 10 | Anchor evidence received after T0 | EVIDENCE_UNAVAILABLE for all | Backfill cannot be treated as first-known at T0 |
| 11 | Missing ref, naive/late/out-of-order proof | EVIDENCE_UNAVAILABLE | Claimed TRUE without provenance is insufficient |
| 12 | Equal inputs/versions | Identical diagnostic JSON | Repeatability is not calibration |

These tests exercise the *shape* of claims, not source truth. A later actual Control engine must derive PROBE → ACCEPTANCE → PERSISTENCE → RETEST → HOLD with NON_RECLAIM from completed, exact-contract bars and independently verify times. It must not accept arbitrary caller-supplied `Evidence(status="TRUE")` as a real confirmation.

## Parameter selection / causal research procedure (not yet run)

The owner-approved v2.2 wording requires a **complete, frozen parameter_manifest** before CONFIGURED status. The current draft contains a field-presence checker but no approved numbers or fitted evidence. The steps needed to reach a reproducible selection are:

1. **Freeze scope first**: `model_id=M1_GPT_STRUCTURE_CONTROL`, spec v2.2, algorithm source SHA, exact instrument/contract capabilities, calendar version, available-at quality, input dataset SHA, snapshot schema, units, inclusion/exclusion, warmup and all tie-breaks.
2. **Freeze finite candidates**: explicitly list values/ranges for ATR/ER, swing window, consolidation lengths, breakout/reclaim/hold, level strength rules, RVOL reference, route, per-horizon `kappa(H)` and target band. Quantiles named in v2.1 are unapproved proposals, **not** assumed values.
3. **Freeze study protocol**: TRAIN session indices and DEVELOPMENT indices with separate roles; named selection metric, coverage metric, minimum samples, uncertainty method, acceptance and tie-break rules; ex-ante `candidate_grid_hash`. Select neither a conveniently successful period nor a threshold after seeing evaluation.
4. **Prove outcome maturity before each fit**: require actual terminal session and receipt availability on or before the fitting boundary. Separately purge overlapping horizon outcomes and apply declared embargo measured in **trading sessions**, not civil days. Synthetic audit implementation conservatively requires 20 excluded sessions between TRAIN and DEVELOPMENT; this is a preflight design choice requiring statistical review, **not a claim** of a universally optimal embargo.
5. **Freeze selected manifest + test on separate governed evaluation**: the existing [M23 protocol](https://github.com/alexpmtk-afk/birzha-mcp-forecast/blob/main/docs/M23_GOVERNED_VALIDATION_PROTOCOL.md) names development 2021–2022 and sealed holdout 2023–2024; 2025–2026 has previously consumed assessments. No holdout or fresh fitting permission is implied by this PR. This change does **not** choose real TRAIN/DEV windows or access those periods.
6. **Do not promote on technical tests**: frozen source/params + real-market validation + independently audited evidence + explicit HOME/production permission are separate gates. Older immutable Forecast/Outcome records must remain unchanged.

The deterministic `SplitPlan` is an **index-level research audit only**. An authorized later implementation must bind each ordinal to an independently established trading calendar and prove available-at/receipt provenance from the raw source. This code intentionally has no network or storage adapter.

## Compatibility decisions left open, not silently implemented

**Market State:** v2.2 internal states are `TREND_UP/TREND_DOWN/BALANCE/TRANSITION/UNKNOWN`; EVENT is an independent flag and ACCUMULATION/DISTRIBUTION are non-exclusive hypotheses. The original Protocol 08 has a six-label display enum. Suggested presentation adapter for later review:

- TREND_UP/DOWN/BALANCE: same display labels if event flag FALSE.
- event_flag TRUE: display `EVENT` as an *overlay* without discarding internal base state.
- TRANSITION/UNKNOWN: no forced projection to BALANCE or invented direction; explicit `UNMAPPED` / `UNAVAILABLE`.
- ACCUMULATION/DISTRIBUTION: explanatory tags only until a separately validated Protocol08 compatibility contract.
- Neither this PR nor Draft #200's six-regime research classifier is admitted as the new v2.2 model.

**Outcome:** v2.2's `TARGET_FIRST / INVALIDATION_FIRST / NEITHER / AMBIGUOUS / INSUFFICIENT_OUTCOME_DATA` plus `GAP_TARGET / GAP_INVALIDATION` and `NOT_ACTIVATED` cannot automatically be renamed to baseline `UP_FIRST / DOWN_FIRST`: boundaries, windows and semantics differ. Require separate versioned outcome namespaces and explicit bridge rules; do not rewrite old outcomes.

## Remaining blockers and next bounded step

- No real parameter candidates or reproducible fitting report; M1 stays **DESIGN_SPEC**.
- No OHLCV stage calculator for anchor/Control/Route; evidence is structurally inspected, not attested.
- No agreed precise M1-to-Protocol08 projection or new M1 Outcome storage contract.
- No permission to use sealed OOS, HOME or production writes.

Next: independent review of these synthetic contracts, then source-grounded finite candidate `parameter_manifest` design and raw-bar causal primitive implementation, with tests against the unchanged governance boundaries. Do not conflate a passing diagnostic preflight with genuine model activation.
