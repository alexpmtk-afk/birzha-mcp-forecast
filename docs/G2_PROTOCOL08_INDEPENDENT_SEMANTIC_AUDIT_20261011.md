# G2 — Independent semantic audit of Protocol 08 v1.0 baseline (2026-10-11)

## Scope and immutable source

I directly re-read the user-owned canonical `08 - Прогнозирование движения цены.txt` on Google Drive (Drive file ID `1q8DYcxl9iiFZxX1UtZRnNYT7GYk7jP8Z`, title `Universal Market Forecasting Protocol v1.0`) on 2026-10-11. Audited exact stacked G2 Draft #198 HEAD `71656aa17c41e1e819e7b41b101bf95aad04052d`, specifically `src/birzha/domain/forecast.py`, `src/birzha/application/forecast.py`, `src/birzha/application/market_state.py`, `src/birzha/domain/market_state.py`, and source-bound G2 staging/witness/audit code. This is **direct code review by ChatGPT**, not signoff by an independent external authorized human reviewer.

The code remains a **non-calibrated directional baseline**. Do not rename the existing Forecast Engine, change previously issued Forecast payloads, or silently promote the release-policy gate. The source document explicitly says **NO TRADE** is a legitimate decision, **Alignment is not probability**, CONTROL must be distinguished from PRESSURE, Route requires typed obstacles/free space, and the 0–15 Alignment scoring allocation is **TBD before Engine 3 acceptance**. Protocol 08 must be judged on its specified semantics, not on presence of similarly named fields.

## Findings from exact code

| Requirement in original Protocol 08 | Audited observation | Status |
|---|---|---|
| MarketSnapshot with source/T0 data provenance | MarketSnapshot V2, original raw source-bound G2 staging captures exist; protected historical original first receipts cannot be recovered | PARTIAL: isolated staging evidence, not every HOME LIVE path |
| Engine 1 regime labels TREND_UP/TREND_DOWN/BALANCE/ACCUMULATION/DISTRIBUTION/EVENT | `build_market_state_vector` returns `classification_status=NOT_CLASSIFIED` and no actual regime classifier | **MISSING** |
| LOCATION relative to exact POC/VAL/VAH | Snapshot normalized features exist, some may use candle-volume-proxy approximate profiles; immutable Forecast fields default to None | **UNVERIFIED for Protocol 08** |
| PRESSURE separate from CONTROL | Derived Delta/flow signals exist but Forecast baseline's buyers/sellers strings do not implement pressure/holding separation | **MISSING independent control proof** |
| Control from REACTION, HOLDING POWER, Effort→Result; confidence 0–10 | `forecast.py` maps the **same combined directional score** directly to `control=BUYERS/SELLERS/BALANCE`; no Control confidence/Holding Engine | **MISSING** |
| Levels with price, role, strength, level history | `ForecastRecord.key_levels` records price-only tuple; no per-level WEAK/MEDIUM/STRONG, acceptance duration or role-flip provenance | **MISSING** |
| Route direction, free-space, typed obstacles and HIGH/MEDIUM/LOW confidence | Baseline `route=TREND` when direction and strength threshold; no obstacle or Route confidence record | **MISSING** |
| Engine 3 Alignment up to 15, mandatory Control >=6/Route != LOW, NO TRADE | No approved Alignment scoring formula exists in canonical source; baseline narrative scenarios are not an Engine 3 risk decision | **SPECIFICATION TBD + ENGINE MISSING** |
| Forecast Entry vs Execution Entry, structure-based stops/targets and reversal as new analysis | Basic primary/alternative narrative, confirmation/invalidation prices, null/empty execution fields; not a completed execution contract | **MISSING** |
| Outcome first touch, MFE/MAE and error roots + 100-forecast learning loop | G2 future outcomes/actual 5/10/20 trading sessions not mature/independently authenticated | **PENDING PROSPECTIVE EVIDENCE** |

### Independent code-level defect found in adjacent provenance collector

`scripts/g2_capture_official_schedule_pages.py` claimed to retain original bytes while using `httpx.Response.iter_bytes()`, which **automatically decodes compressed HTTP content**. An exchange's `Content-Encoding: gzip/br/deflate` would turn the stored SHA into a hash of *decompressed* material rather than exactly received HTTP entity bytes, breaking the stated provenance contract. Fixed with explicit `Accept-Encoding: identity`, `iter_raw()` and conservative refusal of any non-identity response encoding before reading. Added negative tests with compressed-encoding headers. This is a verified source-code defect, not a claim that it occurred historically in a real MOEX response.

## New non-executing protocol acceptance guard

`src/birzha/application/g2_protocol08_semantic_gate.py` evaluates the actual immutable `ForecastRecord` and, optionally, the exact `snapshot_id` + SECID + T0 of the Market State evidence vector. It lists each missing contract explicitly and **never changes** the Forecast payload, starts network requests, or claims full Protocol 08 acceptance. It does not approve the source merely because `validation_status` is caller-modified, or because `control=BUYERS`, `route=TREND` or fictional directional scenario words are present.

Its `evaluate_mandatory_conditions` checks the exact canonical direction/Control consistency, Control confidence threshold >=6, Route confidence != LOW, Balance cannot automatically produce a direction, Alignment 7–<10 confirmation and <7 refusal. **The Alignment formula is intentionally TBD**; even a caller assertion `scoring_formula_approved=True` yields only `CANDIDATE_CONDITIONAL_NOT_EXECUTABLE` if all other conditions pass. It cannot issue an order, change the 12 release gates or turn an uncalibrated score into probability. Malformed score, NaN/infinity, booleans, invalid control/route/state are fail-closed. Trading/execution remains strictly unimplemented.

## Exact finish-line for Forecast Protocol 08

1. Approve an explicit deterministic, backtestable versioned Market State classifier (six regimes) with reliability/quality thresholds before retrospective evaluation.
2. Build **distinct** Control/reaction/holding/effort-result evidence from ex-ante raw data; define transparent confidence 0–10; add positive/negative tests including price movement against pressure.
3. Formalize role/strength/versioned history for POC/VAL/VAH/HVN/LVN/VWAP. Confirm correct exact-contract and exact source time; candle-volume proxy must not silently become true raw-trade volume.
4. Compute obstacle-aware Route and available space only for directional state; confirm route quality HIGH/MEDIUM/LOW from source facts, not the baseline score.
5. Get user-approved **numerical Alignment 0–15 allocation and mandatory conditions** prior to Engine 3; implement NO TRADE, forecast entry vs execution entry, structure stop/targets/invalidation and independently reviewed reversals. Do not invent a scoring formula.
6. Preserve immutable pre-event Forecast receipts; wait for actual matured +5/+10/+20 sessions, then evaluate first touch/MFE/MAE and error root causality separately from execution P&L and maintain minimum 100-future-forecast learning threshold before changing a parameter.
7. Independent external domain review and signoff, genuine timestamp/calendar/clock attestation, authorized HOME read-only acceptance, then separately approved merge and deployment with backup and rollback.

No original Forecast/Outcome modifications, no protected 2023–24 OOS, no production/HOME/main change, no live network traffic or TSA provider. **Machine gate `forecast_protocol08_complete` must remain OPEN; 12 release blockers still apply.**
