# M1 v2.2 — initial specification/code audit (2026-10-19)

**Scope:** first bounded, read-only inventory plus non-executing manifest preflight. No release, parameter fitting, prediction, data copying, historical holdout use, MCP tool registration, DB access, HOME changes, merge or deployment.

## Source priority and identity

- **Current M1 source:** [Project Map / «Модель 1 — инструкция»](https://docs.google.com/spreadsheets/d/1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit#gid=2026101801), revision **2.2 (2026-10-18)**, model_id **M1_GPT_STRUCTURE_CONTROL**, status **DESIGN_SPEC**.
- Historical sources retained without rewriting: «прогноз 2» columns B (original) and I (v2.1); [Protocol 08 v1.0](https://drive.google.com/file/d/1q8DYcxl9iiFZxX1UtZRnNYT7GYk7jP8Z/view), [G2 audit](https://drive.google.com/file/d/1bowodXudF_TZsT3pF6rEofrA1sNvDTn9/view), memoranda 04/06/09.
- Verified `main` HEAD at audit: `fc2572d174be435647fb6a5ea95a5b1611d3d74a`.
- [Model Registry](https://docs.google.com/spreadsheets/d/1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit#gid=713948392) reserves M1–M4; baseline v0.4 remains a separate legacy algorithm, not M1.
- Source-of-truth text remains in Google Sheets; this document records *differences and engineering findings*, not a competing copy of the methodology or project task board.

## Semantic requirement matrix

Statuses are **MAIN_PARTIAL**, **DRAFT_RESEARCH_ONLY**, **ABSENT_MAIN**, **DESIGN_REQUIRED**, **LOCAL_PREFLIGHT_ONLY**; these are not approvals.

| requirement_id | original_rule | v2.1_rule | ambiguity / issue | v2.2 decision / remaining requirement | code_location / destination | tests | status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1-01 | D1→H1→M15, no future | causal anchor and separate windows | unknown historical first receipt | only completed admissible bars with `available_at<=T0`; unknown receipts cannot certify PIT | `snapshot.py`, `stored_session_series.py`; input gate | completed/PIT/receipt negatives | MAIN_PARTIAL |
| M1-02 | ATR/ER/displacement | signs and windows not all consistent | TREND_DOWN symmetry | `sign(DISP)` determines side, `abs(DISP)` magnitude, zero ATR unavailable | `normalized_features.py`; proposed M1 primitives | mirror-up/down, zero ATR | DESIGN_REQUIRED |
| M1-03 | identify movement anchor | historical return-derived `k_confirm` | circular fit and future dependency | explicitly fixed fit/warmup/trailing windows; anchored breakout with `confirm_bars`, no future | new isolated anchor module | T0 prefix-invariance, pending/confirmed | DESIGN_REQUIRED |
| M1-04 | objective swings | future-right-window pivot formula | event time conflated with known time | `event_time`, `confirmed_at`, `available_at`; `swing_w` chosen ex ante | new structural primitives | equal highs, look-ahead | DESIGN_REQUIRED |
| M1-05 | six market regimes | six mutually-exclusive branches | branches can suppress each other | v2.2 uses five base states UP/DOWN/BALANCE/TRANSITION/UNKNOWN, separate event flag and accumulation/distribution hypotheses; compatibility projection to Protocol 08's six names remains an integration design question | `market_state` research in PR #200/#261, not main | reachability, EVENT abstention | DRAFT_RESEARCH_ONLY |
| M1-06 | PRESSURE ≠ CONTROL | PROVISIONAL/REINFORCED and HOLD | overloaded provisional meaning | side/strength/causal_status separated; only fully observed RETESTED control may pass mandatory chain | `forecast.py` currently uses one score (not M1); PR #261 research | pressure without control, symmetric retest | ABSENT_MAIN |
| M1-07 | level price/role/strength/history | level evidence and 3/5 | arbitrary vote and event timing | time-bound level registry, rule-based strength, retest-based role flip; all ties explicit in manifest | `volume_profile.py` partial; PR #261 research | role-flip/ties/source labels | MAIN_PARTIAL |
| M1-08 | obstacles and free route | fixed ATR assumptions | missing levels treated as infinite space | obstacle distance to first zone boundary; UNKNOWN_COVERAGE when no levels | PR #261 research, not main | inside-zone/unknown coverage | ABSENT_MAIN |
| M1-09 | Alignment 0..15 not probability | `evidence_count` 0..5, 3-of-5 | duplicate same-source evidence | v2.2 forbids evidence vote as admission; five groups descriptive only | M1 mandatory-chain module pending | correlated evidence NO_TRADE | DESIGN_REQUIRED |
| M1-10 | 5/10/20 horizons | `kappa(H)`, `band_hi` undefined | risk of future fit and horizon copying | three independently admissible session-counted horizons; fit-only expected move, per-horizon targets | `prediction.py` baseline; new M1 horizon module | separate targets and missing H | DESIGN_REQUIRED |
| M1-11 | immutable forecast | additional v2.1 fields | legacy journal schema vs M1 records | separate versioned M1 record/namespace without rewriting existing forecasts | `domain/forecast.py`, `journal.py`, `storage/forecast_journal.py` | immutable/idempotent/backcompat | MAIN_PARTIAL |
| M1-12 | first-touch, MFE/MAE | UP_FIRST/DOWN_FIRST | direction-barrier outcome vs target/invalidation outcome | v2.2 defines TARGET_FIRST/INVALIDATION_FIRST/... including GAP and ambiguous OHLC: separate versioned outcome contract; legacy first-touch must not be silently relabeled | `application/outcome.py`; new M1 outcome contract | same-bar double touch/gap | DESIGN_REQUIRED |
| M1-13 | OOS separate from learning | recalibrate with OOS quantiles | look-ahead and consumed holdout | TRAIN/DEVELOPMENT/FINAL_HOLDOUT + purge/embargo; do not open M23 2023-24 holdout | `validation.py`, `docs/M23_GOVERNED_VALIDATION_PROTOCOL.md` | no-leak/frozen candidate | MAIN_PARTIAL |
| M1-14 | no numeric guessing | multiple implicit 0.5/0.75/3-of-5 | no complete parameter manifest | explicit fields; CONFIG_REQUIRED until filled; schema-complete still REVIEW_REQUIRED, never forecast authorization | `application/m1_parameter_manifest.py` | `tests/test_m1_parameter_manifest.py` | LOCAL_PREFLIGHT_ONLY |
| M1-15 | six-market sources, exact contracts | profile/Delta/OI assumed available | indexes may lack volume, candles are proxy profile | capability mask, raw/proxy labeling, exact futures SECID, no manufactured values | `forecast_input_readiness.py`, `volume_profile.py`, PR #263 isolated data proposal | absent-flow/index/rollover | MAIN_PARTIAL |
| M1-16 | MCP and agent explanation | model-selection calls | name collision with v0.4 baseline | shadow/read-only M1 after full model implementation; MCP/skills are not currently installed M1 | `mcp/server.py`; PR #264 *draft* model registry | tools/list after permitted HOME install | ABSENT_MAIN |

## Verified code and PR boundaries

- `src/birzha/application/forecast.py` on verified `main` labels its algorithm `BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS`; `build_forecast_from_snapshot` maps the same weighted score to UP/DOWN and buyers/sellers CONTROL, and sets ROUTE to TREND/BALANCE by strength. **This does not satisfy M1 evidence semantics.**
- `src/birzha/domain/forecast.py` contains an immutable v1 record with optional future-engine fields, not the accepted new M1 output contract.
- Existing `forecast.build/create/get` and `outcome.evaluate` in `src/birzha/mcp/server.py` are baseline-facing. A GitHub Draft cannot establish installed HOME tools.
- Draft PR [#199](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/199) semantic guard; [#200](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/200) six-state classifier; [#203](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/203) **unapproved** Alignment15; [#261](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/261) unified research; [#263](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/263) isolated data schema; [#264](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/264) M1–M4 metadata are **open Draft PR**, not main/HOME.
- Google cells `'прогноз 2'!I593:I594` were **re-read as literal strings**, not current formula errors. No correction applied, and historical source untouched.
- Actual HOME source SHA, installed MCP tools/list and forecast data contract remain **unverified** in this cloud-only audit.

## What this branch implements

`audit_m1_parameter_manifest` is isolated and side-effect free. It checks presence and basic well-formedness of documented *proposed* fields. It never chooses numeric parameters, constructs predictions, returns release approval, reads historical records or installs MCP. The synthetic test fixture values are NOT parameters proposed for the real model. Even a complete syntactic object returns `REVIEW_REQUIRED` and `forecast_allowed=False`.

**Next bounded step:** develop explicit transition-example fixtures and full parameter provenance/selection protocol using allowed TRAIN/DEVELOPMENT only; resolve the enum/outcome compatibility contract; then independently review the frozen candidate and only afterwards implement causal engines. Actual M1, CI, HOME and OOS acceptance are all unproven.
