# M1 v2.3: names, schema drift and quality audit (19 October 2026)

**Source of authority**: [КАРТА ПРОЕКТА — БИРЖА](https://docs.google.com/spreadsheets/d/1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit), active tabs “Модель 1 — инструкция” (rows 116–118, appended non-destructively) and “прогноз 2” (K4:L5 and K49:L49; historical B/I preserved). Specification v2.3 remains **DESIGN_SPEC**, not an approved parameter profile.

## Independent live inventory

On 19 Oct 2026 all 18 canonical `D1_INDICATORS`, `H1_INDICATORS`, `M15_INDICATORS` headers in SBER, Si, BR, GOLD, IMOEX and RTSI were read directly. Every tab contained exactly the **same 54 unique field names** in the same physical order at the time of inspection; none contained a duplicate. Existing `D1/H1/M15` source sheets had 28 columns and their sizes were unchanged. This DOES NOT authorize future code to rely on that order. Always read a fresh header.

The old technical map incorrectly identified `formula_id` as the next physical column (and several neighboring fields as shifted). Updated live documentation defines **names**, not fixed letter/column positions. Current fields are explicitly grouped as: 20 core indicator metadata fields, 14 legacy values and 20 separate ATR research fields. `atr_14_sma` != `atr_14_wilder`; `volume_ratio_20` != `m1_rvol_median`; `price_location_20` != `m1_close_position_1bar`. Existing `return_5/10/20` are strictly outcome labels and not T0 features.

`src/birzha/application/m1_named_fields.py` is an isolated, read-only research contract helper; it is **not installed** in NativeCanonicalSheets or the existing Forecast Engine. Its caller must verify sheet identity/ownership and actual read/write permissions. It provides: exact header resolution and duplicate/missing/unknown guards, projection of arbitrary header order, explicit named-field row builder, key-based full-content backread (missing/duplicate/partial-write/mismatched values all fail), and future-outcome selection guard. A1 notation can be produced only *after* resolving the actual header; it never defines field meaning.

## ATR evidence and limitations

Verified after v2.3 formula replacement: all 18 indicator tables contain Google Sheets dynamic formulas (`LET`, `SCAN`, `ARRAYFORMULA`, `HSTACK`, `XLOOKUP`), and a second independent audit recorded **237,665 populated research ATR values** and **2,281 independent sample comparisons with zero numerical differences** (to 1e-5). This is neither a full independent replay of every historical ATR value nor proof of incremental primary indicator identity creation. Existing `_atr_calc_key` is the key of a chronologically sorted calculation series, not a same-physical-row foreign key. Raw/source identity must be joined through `source_record_key` / `atr_source_record_key`, not by matching row indices.

An isolated test book demonstrated that a new SBER bar got correct Wilder value `5.45850032` via formula but legacy identity fields on the corresponding indicator row were absent, with `INDICATOR_ROW_MISSING`. Thus numerical formula continuity and full immutable record creation are **separate acceptance conditions**. Production calendar, original first-seen/available_at, source window hash, validated exchange session gap, and strict PIT remain unproven. Use `RESEARCH_ONLY;SEQUENCE_UNVERIFIED;CALENDAR_UNVERIFIED;AVAILABLE_AT_UNVERIFIED` where evidence is missing. Do not regenerate synthetic historical receipts or upgrade flags.

## Explicit M1 semantic delta: old to new

| old_rule | new_rule (v2.3) | reason / implementation_effect | compatibility / test |
| --- | --- | --- | --- |
| Six mixed state labels, including ACCUMULATION, DISTRIBUTION, EVENT | Regimes TREND_UP, TREND_DOWN, BALANCE, TRANSITION, UNKNOWN; EVENT separate exclusion; accumulation/distribution only diagnostics | Distinguish regime from event/pressure; avoid silent renames | Version every state; no automatic compatibility mapping; tests must assert EVENT never becomes regime |
| PROVISIONAL control may have been treated as evidence | BUYERS/SELLERS must pass PROBE→ACCEPTANCE→PERSISTENCE→RETEST→HOLD, strength RETESTED | Initial contact alone is insufficient | Never remap PROVISIONAL to RETESTED; assert initial-only not ACTIVE |
| Delta/OI/FUTOI part of a possible mandatory vote | Price-only base may proceed without optional FLOW/OI/FUTOI | Index data often lacks trade aggressor/OI | Missing diagnostics should be UNAVAILABLE/NOT_APPLICABLE, not silently manufactured |
| 3-of-5 evidence score | Explicit TRUE/FALSE/UNAVAILABLE Mandatory Chain (all TRUE required) | No probabilistic vote can override missing level/clock/calendar | Find and test legacy 3-of-5 entrypoints before authorizing runtime |
| One unversioned H target | Independent 5/10/20 complete trading sessions, with frozen kappa/band/risk and manifest | Cannot copy targets or tune on final holdout | Unknown numeric profile = CONFIG_REQUIRED, forecasts inactive |

These are research transition requirements; no claim that PR #261, #265–#270 were fully migrated or independently integrated at v2.3. Historical versions stay immutable.

## Repository fixes still NOT accepted

- **Q-134**: PR [#262](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/262) head `972df0b59e7dcc522527b81851525a1046a14dca` contains service-sheet post-commit validation fix. It remains **open, unmerged**. A source-level test/CI does not prove deployed worker behaviour.
- **Q-135/Q-136**: PR [#263](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/263) head `37cf2debb042d67e38580df36ad701d5b5ac3417` stacks on #262 and reports preserved snapshot data after corrected batch payload. Independent packet preflight, authenticated provenance, real backread of every canonical workbook, and retry idempotency must still be separately evidenced; this PR remains open Draft.
- **#261** and standalone **#265–#270** are parallel research branches; no silent merge, version conversion, Forecast Engine override or model activation. Unapproved `parameter_manifest` and historical strict PIT block ACTIVE.
- Future outcomes `return_5/10/20` are prohibited from T0 features regardless of the availability of numeric values.

## PASS criterion of this bounded PR

Review isolated tests: header order permutations, missing/duplicate/unknown field fail closed, no implicit writes, partial/duplicated/wrong-key backread, arbitrary column address calculation, and future outcome leakage prevention. CI on exact SHA must PASS. Other components are explicitly outside scope; the overall owner request remains **PARTIAL** until the named transport/API paths adopt the resolver, synthetic mutation/append tests cover actual Sheets formulas, calibrated manifest is accepted, and a separate authorised runtime/migration review is complete.

No raw OHLCV, canonical forecast records, HOME, DuckDB, secrets, production source, server installation, OOS, deployment or merge were touched by this PR. No canonical worksheet data change was made by the code here.
