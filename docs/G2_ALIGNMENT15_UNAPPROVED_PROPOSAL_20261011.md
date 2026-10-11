# G2 / Protocol 08 — Alignment 15 points: candidate for user approval, NOT enacted (2026-10-11)

## Grounding

The user-owned Google Drive canonical `08 - Прогнозирование движения цены.txt` (**Universal Market Forecasting Protocol v1.0**) lists seven **candidate factors** — Market State, Control, Location, Pressure, Efficiency Ratio, Effort→Result, Route Confidence — and a maximum score 15, but explicitly says **"Alignment scoring formula = TBD before Engine 3 / Scenario + Risk acceptance"**. Protocol threshold statements: 10–15 strong, 7–<10 moderate requiring confirmation, <7 NO TRADE. Independent mandatory conditions must hold for LONG: BUYERS Control, Control confidence >=6, Route confidence != LOW; mirror for SHORT. **Alignment is not probability**. A scenario must separate Forecast Entry from actual Execution Entry, place invalidation/stops at structural failure, consider structural targets and never auto-reverse.

Neither the text nor previous independent work approves any allocation, calibration, or automatic trade. The seven weights proposed below are **invented as an explicitly named, versioned engineering candidate** for review; they are not asserted to be the canonical rules.

## Unapproved allocation (max 15)

| Factor | Max | Strict candidate points |
|---|---:|---|
| Market State | 2 | 2 if causal provisional TREND_UP for LONG or TREND_DOWN for SHORT; 0 for opposite trend; all other regimes, insufficient history or ABSTAIN => **missing** |
| Control | 3 | 3 if independently observed matching reaction + 2 holds and research control score >=8; 2 if >=6; 1 if <6; 0 if opposite held side; unavailable => **missing** |
| Location | 2 | LONG: ABOVE_VAH=2, INSIDE_VALUE=1, AT_POC/BELOW_VAL=0; SHORT mirrored. Must be from **matching exact public-trade profile** with causal flow; no profile/candle-volume proxy/forged exact => **missing** |
| Pressure | 1 | 1 if causally matched PASS Delta pressure favors scenario, else 0 if available and contradictory/neutral. Missing or degraded flow => **missing**, never fabricated neutral |
| Efficiency | 2 | Mean of available D1/H1/M15 20-bar Efficiency Ratio >=0.65 =>2, >=0.4 =>1, else 0; any missing/invalid => **missing** |
| Effort→Result | 2 | Independently established reaction and holds: opposite Control =>0; matching Control with reaction volume / prior 20 completed bars >=1.25 =>2, >=1 =>1, otherwise 0; absence of reaction/volume => **missing**. Unsigned aggregate candle volume is **not** buy/sell tape |
| Route | 3 | 3 for independently recomputed same-snapshot Route HIGH, 2 for MEDIUM, 1 for LOW, 0 for opposite directional route; ambiguous/no verifiable obstacles => **missing** |

**Missing required evidence != 0:** only compute a numerical sum when all seven are valid. Always compute LONG and SHORT separately; a draft 15/15 for either side **does not satisfy** mandatory conditions or independently validate a market edge. Treat no known obstacle as no verified Route, not unlimited reward. The proposed weights, thresholds and discretion to treat absent Delta as missing are subjects for explicit approval and prospective calibration, not facts from the original protocol.

## Safe, non-executing code

`src/birzha/application/g2_alignment15_proposal.py` internally recomputes state/holding/route from a single actual MarketSnapshot/Instrument/M15 CandleSeries, checking that exact snapshot ID, SECID and T0 match. It accepts **no external claimed scorecards**, does not change old Forecast, and uses only the Profile levels that Route admitted with same-flow exact-method source. `scorecards` show factor-by-factor points/max and explanations if unavailable.

Until policy approval, output always `selected_decision=NO_TRADE`, `user_scoring_formula_approved=false`, `engine3_accepted=false`, `independent_source_and_clock_attested=false`, `scenario_or_execution_authorized=false`, `production_authorized=false`; `primary_scenario, alternative_scenario, forecast_entry, execution_entry, stop, invalidation, reversal` stay NULL and targets empty. **No synthetic entry/stop/target, investment recommendation, external network request, execution API, protected holdout or fake future outcomes.** Even a high candidate score is a research diagnostic.

### Example on synthetic source data only

An invented buyer Control (high confidence) with bullish research regime but seller-favored Delta and a profile location BELOW_VAL can sum **12/15 for LONG**, while SHORT sums **5/15**. This is not an instruction to trade: a 12/15 research score can coexist with major profile disagreement, unapproved formula, unauthenticated provider/clock and missing market calibration, and the result remains NO_TRADE.

## Requirements for true Engine 3

1. User explicitly approves/revises each of the seven numeric weights, per-factor scoring and missing-evidence policy, on a stable versioned contract. **Do not infer consent from "continue".**
2. Independently verify and calibrate reliability and mandatory Control/Route conditions with untouched future prospective first-receipt forecasts and final session outcomes; no retrospective optimizer used on protected 2023–24 holdout.
3. Specify causal activation/forecast entry and actual execution entry separately, structure-based stop/invalidation, typed targets, partial exit logic and reset-before-reversal. Provenance: instrument/contract, exact SECID/time/calendar, original source evidence and genuine TSA.
4. Only then do independently reviewed acceptance, explicit main merge authorization and separate HOME deploy permission. Until then, `forecast_protocol08_complete` OPEN, all **12 G2 release blockers** remain.

Sources: user-owned original Protocol 08 (not amended) and chained Draft #202, #201, #200, #199. This document is a **decision proposal** rather than an approved trading methodology.
