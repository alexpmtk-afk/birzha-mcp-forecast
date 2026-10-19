# M1 v2.2 — price-control causal chain (isolated research)

Source of truth: [Project Map → «Модель 1 — инструкция», revision 2.2, §§11–13](https://docs.google.com/spreadsheets/d/1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit#gid=2026101801). This document records implementation choices and tests; it is **not** a competing model specification.

## Chain implemented

The new `src/birzha/application/m1_control_chain.py` works from input `CausalBar`, `AnchorBinding`, `FrozenLevel`, `ControlParameters`; it is dependent on Draft #267/#268 types only, never on live MCP, the journal, network or HOME.

For the positive BUYERS direction, it accepts only successive *distinct* bars for these stages:

1. **PROBE**: an eligible observed bar intersects the frozen level zone [lower,upper] and its close has not yet accepted the bullish side.
2. **ACCEPTANCE**: later bar closes strictly `>upper`. The first accepting bar is not used simultaneously for PROBE.
3. **PERSISTENCE**: `confirm_bars` subsequent consecutive closes strictly `>upper`, not including the ACCEPTANCE bar. Interrupted persistence resets the chain.
4. **RETEST**: a later bar's [Low,High] intersects the original zone and its close remains strictly `>upper`; a close in/at the zone invalidates this conservative research candidate.
5. **HOLD**: `hold_bars` subsequent consecutive closes strictly `>upper`. The RETEST bar is never also the first HOLD bar.

For SELLERS the complete logic is mirrored (`<lower`). This deliberately conservative counting resolves ambiguities in v2.2; **it remains a provisional interpretation requiring independent domain acceptance**, not a newly approved production rule.

`NON_RECLAIM` after ACCEPTANCE checks all subsequent completed, available bars through T0: BUYERS chain invalidated when close `< lower - reclaim_buffer_atr * atr_frozen`; SELLERS when close `> upper + reclaim_buffer_atr * atr_frozen`. Equality at the reclaim threshold is not an explicit reclaim; interruption of PERSISTENCE or HOLD is independently checked. A revoked chain cannot resume without a fresh PROBE. `chain_max_age` counts observed bar positions from the PROBE index, including bars after HOLD. This is not a substitute for a verified session calendar.

The anchor's direction and exact SECID must match; level ID, source reference, zone, ATR and confirmation clocks remain fixed and must have been available **before** the PROBE bar. The first observed T0-inadmissible or unattributed bar stops the complete result with `INPUT_UNAVAILABLE`, rather than skipping it. An appended future bar cannot rewrite earlier T0 conclusions. Every stage records its own event/confirmation time, source reference and stated receipt availability. No pressure/delta evidence is used to assert control.

## Output and safety

The result carries `control_side=BUYERS|SELLERS|UNCONFIRMED`, `control_strength=NONE|INITIAL|RETESTED`, `causal_status=PENDING|CONFIRMED|UNAVAILABLE`, timestamped stage evidence, the last reclaim-check observation and the level/anchor identifiers. **CONFIRMED_RESEARCH_ONLY** means only that this internal state machine has found a matching pattern in *caller-supplied evidence*. It is **never an operational forecast confirmation or a proof of source availability**; `calendar_attested=False`, `source_receipts_attested=False`, `forecast_allowed=False` unconditionally.

Not implemented: determining a valid D1 regime, official level registry, external evidence attestation, proof of absent candle completeness by calendar, M15-to-H1 retest identity/aggregation, route, target, 5/10/20 outcome admission, fit/holdout, publish or deployment. No existing baseline code, Forecast/Outcome Journal or database is modified.

## Verification

Run `python -m pytest -q tests/test_m1_control_chain.py` then full GitHub repository CI. Synthetic cases test direction mirror, unique stage chronology, pending INITIAL, strict RETEST/HOLD, invalidation and re-probe, expiry, exact level/contract, delayed receipt, incomplete bars, future bar invariance, malformed thresholds and deterministic repeatability. Synthetic parameter numbers are **not accepted model parameters**.

Next required step: independently audit strict stage counting, then bind the module to verified anchor/level events from the causal data layer, and only later register a separately versioned M1 research read-only surface. Draft PRs do not authorize merge/install.


## Actual algorithm-to-algorithm binding

`bind_m1_research_anchor()` only accepts a `CONFIRMED_RESEARCH_ONLY` result from the prior `scan_m1_consolidation_anchors()` contract. It carries direction, original event/confirmation/availability clocks and the **full breakout confirmation source-ref tuple**, rather than allowing a bare direction flag to stand in for the anchor. It rejects PENDING anchors, missing source chain, nonsensical causal timing and wrong model result. A synthetic integration test now executes **both** independent algorithms sequentially (anchor -> binding -> control).

The level zone is still an independently supplied, pre-confirmed FrozenLevel. This intentionally avoids silently promoting the prior D1 consolidation band into an H1/M15 level or claiming cross-timeframe calendar proof. The adapter is type/causality enforcement, not an authenticating receipt verifier; `source_receipts_attested=False` and `forecast_allowed=False` remain invariant.
