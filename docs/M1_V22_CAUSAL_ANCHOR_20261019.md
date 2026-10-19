# M1 v2.2 — causal consolidation / anchor research primitive

Source of truth: [«КАРТА ПРОЕКТА — БИРЖА» → «Модель 1 — инструкция», revision 2.2](https://docs.google.com/spreadsheets/d/1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit#gid=2026101801). This is an implementation note, **not** a competing official specification.

Depends on the isolated Draft PR chain #265 (manifest preflight), #266 (diagnostic control/research) and #267 (CausalBar / swing candidates). No merge into `main`; no HOME, MCP, Forecast/Outcome journal, DuckDB, real data or protected OOS writes.

## Implemented research-only logic

1. Input is exact-contract, exact-timeframe `CausalBar` with original OHLC, `start/end`, `available_at`, `is_complete`, `source_ref`. If a bar with `end<=T0` is incomplete, first-seen-unavailable or unattributed, **stop** and report `INPUT_UNAVAILABLE` rather than compressing the data. Bars after T0 cannot change an old T0 result.
2. Wilder ATR(14) requires 14 true ranges with a prior close, as specified by v2.2; no assumed zero-valued prior close, no division when ATR is zero. Before the first ATR, return `WARMUP`.
3. For each potential breakout at index `j`, consolidate only over windows `[j-n, j-1]`, with reference previous close `j-n-1`. Calculate `ER(n) = abs(close[j-1]-close[j-n-1])/sum(abs(close[k]-close[k-1]))`. Calculate `(maxH-minL)/ATR(j-1)`; both must satisfy explicitly supplied, non-calibrated thresholds. If multiple lengths pass at the same endpoint `j-1`, prefer longest. This is the v2.2 tie rule for identical endpoints.
4. Freeze consolidation upper/lower and `ATR(j-1)` before bar j. A breakout UP requires strictly `C(j)>upper+breakout_buffer_atr*ATR`; DOWN requires strictly `C(j)<lower-breakout_buffer_atr*ATR`. Equal threshold does not break out.
5. **Proposed precise confirmation interpretation:** `confirm_bars` includes the breakout close itself; all its closes must remain strictly outside the **frozen zone boundary**, not necessarily outside the initial ATR-buffered breakout line. This choice removes an ambiguity and **requires independent acceptance before operational use**.
6. **Proposed precise reclaim interpretation:** after breakout and through observed T0 prefix, cancel UP if a close is `<= upper-reclaim_buffer_atr*ATR_frozen`; DOWN mirrors `>=lower+reclaim_buffer_atr*ATR_frozen`. A close within the zone but not yet through reclaim threshold interrupts pre-confirmation consecutive closes; after confirmation it does not retroactively change the original observation, but reclaimed confirmation is revoked. These are **candidate semantics**, not approved fitted rules.
7. If fewer future admissible closes are available than needed, mark `PENDING`, give the **next required ordinal index** rather than inventing a trading-session timestamp. If a candidate is both verified and still valid at T0, choose the **latest breakout**. Later pending candidates may appear separately; they do not reclassify an older confirmed anchor.
8. Keep `event_time` (breakout bar end), `confirmed_at` (last confirmation bar end) and `available_at` (last necessary actually available receipt) separate. The actual intrabar breakout tick time is not claimed.

**Non-goals:** no gap/calendar attestation (a physically missing bar cannot be detected by this function alone); no contract-roll resolution; no source receipt or historical first-known attestation; no session-level horizon outcome, hypothesis tests, fitted numeric settings, profit metrics, forecast decision or MCP registration. A caller-supplied `available_at` is only a claim until validated by the upstream source-bound adapter.

## Examples and acceptance boundaries

- 20 synthetic flat bars with 2-point high-low interval, then closes 104 and 105 above frozen upper 101: `UP` candidate, `PENDING` after first close, `CONFIRMED_RESEARCH_ONLY` after the second.
- Mirror closes 96 and 95 beneath lower 99: `DOWN` candidate using identical logic.
- Later confirmed reclaim, late receipt or missing source: cancellation / `INPUT_UNAVAILABLE`, not an active forecast.
- Equal breakout price to threshold: no candidate; larger passing consolidation window wins with same endpoint; future bars beyond T0 do not change previous output.
- All input thresholds in tests are **synthetic**. They are not chosen M1 values and must not be imported as production defaults.

## Verification and next stage

Run `python -m pytest -q tests/test_m1_causal_anchor.py` followed by full repository CI on the exact PR head, then source review and proof of no baseline integration. The next bounded stage can derive condition/state candidates from independently verified bars and a **frozen complete parameter manifest**. Until independently reviewed and calibrated, model lifecycle remains `DESIGN_SPEC`, regardless of technical PASS.
