# M1 v2.2 — causal route / structural obstacle research (2026-10-19)

**Canonical methodology**: [Project Map → «Модель 1 — инструкция», rev. 2.2, §§10/12–14](https://docs.google.com/spreadsheets/d/1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit#gid=2026101801). This is a technical implementation ledger, not a competing source of rules or a new task board.

**Scope**: isolated algorithm in `src/birzha/application/m1_route.py`, test fixtures in `tests/test_m1_route.py`; stacked atop Draft #269 and earlier Draft #265–#268. No update to existing baseline, model selection, Forecast/Outcome journal, historical storage, MCP registration, HOME or Google Sheets.

## Deterministic price-only route calculation

- **P0** is an explicitly supplied, finite, time-stamped source-referenced reference price, not promised execution. Its observation and receipt must be at or before T0. Direction is explicitly `UP` or `DOWN` and is **not inferred from scores** in this module.
- **Structural zone** is a caller-supplied immutable identity and a frozen [lower,upper] price interval, exact SECID, timeframe, type, role, quality, source ref, event/confirmation/availability clocks. Only `SWING`, `CONSOLIDATION`, `DAILY_EXTREME`, `WEEKLY_EXTREME` from `PRICE_STRUCTURE` with `CONFIRMED` role/quality can become candidate structural barriers. No role or source is manufactured from price direction. Duplicated IDs or mixed contracts are rejected.
- **Forward zone / closest boundary**: for UP, a zone ending below P0 is behind price; a remaining zone's distance is `max(0,lower-P0)`. For DOWN, a zone starting above P0 is behind; otherwise distance is `max(0,P0-upper)`. An interval enclosing/touching P0 yields **0**, never distance to the center. UP obstacles require known `RESISTANCE/BOTH`, DOWN known `SUPPORT/BOTH`. Unknown role / provisional quality is retained as an *unresolved possible barrier*, not cleared without proof.
- **Selection**: ascending absolute price distance, then **earlier** `confirmed_at`, then `level_id` lexicographically. This follows the explicit model tie-break principle. No near-level merging is attempted absent approved `level_merge_atr` and merging algorithm. Multiple original zones remain distinguishable.
- **Missing coverage**: no qualifying structural obstacle inside the declared price envelope means `UNKNOWN_COVERAGE`, **not** `CLEAR`, `FREE`, or an infinite open route. A nearer/equally distant unresolved structural zone yields `UNRESOLVED_FORWARD_ZONE`, not an admitted obstacle. Later available levels are invisible at earlier T0.
- **Diagnostic-only POC/HVN/LVN**: `TRADES_BASED` and `CANDLE_VOLUME_PROXY` profile types are carried separately as optional context. Neither becomes a compulsory price-route obstacle in baseline v2.2, even with `CONFIRMED` in the user-provided record. Any future broader use requires a separately versioned capability and tested protocol.

## Independent horizon evaluation and explicit boundaries

The caller must specify `horizon_scopes` for H5, H10, H20 separately. Each scope is a **strictly positive finite price-distance envelope**, not a fitted forecast amplitude, target, ATR multiple, or hidden fallback. An absent horizon scope returns `CONFIG_REQUIRED` for that horizon only; no copying H20 to H5. Inside a supplied scope, the module reports candidate structural obstacle count and nearest obstacle. It does **not** calculate density classification HIGH/MEDIUM/LOW: a frozen density interval and calibrated thresholds remain absent.

These three scopes are synthetic in tests. In a later valid M1, they may only be sourced from a separately frozen, source-attested horizon module (e.g., fit-only `E(H)` and approved `band_hi`); simply supplying a broad envelope does not make any target reachable. M1's risk, opposite-side invalidation, reward/risk and all final scenario gates are not computed here.

No independent calendar completeness or actual source receipts are confirmed. The returned `levels_attested`, `coverage_attested`, `source_receipts_attested`, and `forecast_allowed` remain **False**, even for `STRUCTURAL_OBSTACLE`. That status means only a consistent *claimed* zone geometry in this research function, not an actionable forecast.

## Verification and follow-on

Run `python -m pytest -q tests/test_m1_route.py` and full repository GitHub Actions checks on exact head. The synthetic tests cover mirrors, nearest boundary / inside zone, missing/late/future levels, role quality, profile exclusion, equal-price tie-break, exact SECID, horizon isolation, no-level UNKNOWN_COVERAGE, malformed thresholds and deterministic permutation.

**Next bounded step**: an independently approved, causal versioned Level Registry (with role-history and quality) feeding this function; then a versioned H5/H10/H20 target/invalidation research contract with strictly TRAIN/DEVELOPMENT-fitted amplitude and embargoed outcome maturity. Before publishing, verify D1/H1/M15 shared retest evidence, state/event, frozen full parameter manifest, independent raw-data admission and immutable outcome semantics. No Draft PR authorizes merge or deployment.
