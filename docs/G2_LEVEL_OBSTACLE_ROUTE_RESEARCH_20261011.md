# G2 / Protocol 08 — causal Level & Route research (2026-10-11)

## Input contract and scope

Builds on Draft #201 Control/Holding and #200 separate Market State research candidates; no Forecast V1 changes. The user-owned canonical `08 — Прогнозирование движения цены` requires type, role, strength and history for market levels; obstacle-aware Route and explicit uncertainty. This Draft implements a strictly **opt-in, offline Level/Route research evaluator**; numerical thresholds are an engineering proposal, not preapproved calibrated market policy. No live calls to MOEX/TSA, orders, automatic entry or trade execution.

`assess_levels_and_route(snapshot, instrument, m15_series)` *recomputes* **both** independent Control/Holding and Market State from the exact same frozen MarketSnapshot and already observed causal M15 CandleSeries. It refuses forged caller-supplied classification dictionaries; no duplicate source fetch. The Control component enforces SECID/board/engine/market identity, 50 completed M15 bars, no unobserved/partial/future candle, and a genuine prior-level reaction followed by two observed holds. The Market State classifier needs its own multi-timeframe data quality and may return ABSTAIN; that also blocks Route.

## Typed levels

For a Control candidate only, generate `PRIOR_SUPPORT` and `PRIOR_RESISTANCE` from **20 candles completed before** the final three-candle reaction/holding window. Each record includes:
- price, role `SUPPORT`/`RESISTANCE`, `WEAK`/`MEDIUM`/`STRONG` **research label** based on 0–1/2/3+ distinct held tests **after** that exact rolling-extreme price first became observable;
- an explicit immutable list of original candle end times, `LEVEL_TEST_HELD` events, evidence from finished M15 bars, and a cooldown preventing adjacent bars being counted as independent repeated tests;
- `role_flip_history_status=NOT_ESTABLISHED_FROM_ROLLING_EXTREME`, because this rolling extremum does **not** actually prove when a prior support turned resistance. Never fabricate a historical role flip.

Optional `POC/VAH/VAL/HVN/LVN` obstacles are admitted only if a `PUBLIC_TRADES_PRICE_QUANTITY_V1` profile matches the **same** original PASS flow record, exact snapshot SECID/symbol and a nonfuture offset-aware flow timestamp, and normalized features explicitly identify the exact public trades method. A candle-derived proxy profile is **excluded** from Route obstacles; a false declaration of exact profile without matching flow provenance blocks the entire Route. Even a matching profile has `source_trust_independently_attested=false` and is not proof of exchange-certified trade origin.

## Route logic and conservative limits

- **BUYERS Control + compatible causal Market State** => consider `UP`; **SELLERS Control + compatible state** => `DOWN`. BALANCE/EVENT/ABSTAIN, contradictory direction or missing Control blocks route even if a previous heuristic Forecast said TREND.
- Original completed prior highs/lows and admissible source-typed profile nodes ahead of current price are ranked by distance in original ATR price-scale units. Levels behind current price are **not obstacles ahead**; future new levels are never invented.
- No known obstacle ahead means **ABSTAIN**, not infinite free space. Nearest obstacle distance less than 1 ATR => ABSTAIN. 1–<1.75 ATR => research LOW, 1.75–<3 ATR => MEDIUM, >=3 ATR => HIGH. Distances are transparent but **not historical win probabilities, nor an approved risk/reward decision**; true order-book liquidity, news, dynamic level tests and time-to-arrival are unknown.
- Output includes typed ahead obstacles, nearest level, candidate free ATR, Control/Market State source versions, missing-evidence reason codes and full `source_independently_authenticated=false`, `route_thresholds_externally_calibrated=false`, `alignment_approved=false`, `scenario_or_trade_authorized=false`, `production_authorized=false`.

Offline tests cover both directions, complete pre-reaction event history, no look-ahead, near obstacle, unknown free space, missing/partial source, contrary regimes, BALANCE abstention, proxy volume-profile exclusion, bogus exact profile fail-closed and matching exact flow profile with acknowledged external trust limit. Tests prove code contracts on synthetic examples, **not independent source truth, profitability or future forecasting quality**.

Remaining real Protocol 08 scope: verified level role-flip and multi-test provenance from longer original preserved tick/price-volume history; independent validation/calibration of Control/Route on prospective post-T0 +5/+10/+20 outcomes; separate approved Alignment 0–15 scoring table; first genuine source/TSA attestation and exact MOEX exchange session proof; independent domain review; recovery and authorized HOME acceptance; separately authorized main merge and deploy. **All 12 G2 release blockers remain open.**


## Q-084: first-observable price and post-discovery history (2026-10-11)

Version `LEVEL_HISTORY_RESEARCH_V2` / `OBSTACLE_ROUTE_RESEARCH_V2` corrects a
specific hindsight problem: the final rolling-20-bar low/high price used to be
applied retroactively across all preceding bars, allowing near-touches
**before that exact extremum first appeared** to boost its strength.

Research history now records the first completed source M15 bar containing the
eventual exact min(low) / max(high) and its offset-aware `observed_at` as
`price_first_observable_at_utc`. The discovery bar is **not** counted as a
separate confirming retest. Only subsequent chronologically observed completed
bars with qualifying reaction/hold, and the pre-existing one-bar separation,
may count as distinct tests. An out-of-order or duplicated observation time
makes all candidate price-level strengths `UNVERIFIED` and the Route ABSTAIN.

**Crucial evidence distinction:** an archived bar and its `observed_at` can
indicate when the *price* became observable within this source dataset; they
do NOT prove that the system recognized the level or published a forecast at
that historical moment. Labels explicitly say
`POST_FIRST_OBSERVABLE_TESTS_RECONSTRUCTED_NOT_EX_ANTE_CERTIFIED` and
`level_detected_ex_ante_proven=false`. The historic source may still lack
independent first-receipt proof, so no ex-ante/prospective qualification follows.

Synthetic negative tests cover late-created supports and resistances with
many earlier near-touches, an uncounted discovery candle, the regular
post-discovery tests, and disordered `observed_at` producing fail-closed
ABSTAIN. Existing control reaction, 15-point draft Alignment weights,
`NO_TRADE`, Forecast V1, OOS, and all 12 release gates are unchanged.

Remaining: independent origin/clock attestation, a live event-time level
journal, real level role-flips, calibration by instrument/regime and untouched
prospective 5/10/20 horizons. NO merge, HOME deploy or production approval.
