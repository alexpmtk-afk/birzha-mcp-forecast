# G2 / Protocol 08 — Research Control/Holding evidence (11 Oct 2026)

## Purpose

The current Forecast v0.4 derives both `control=BUYERS/SELLERS/BALANCE` and `route=TREND/BALANCE` from its **combined directional score**. That shortcut does NOT satisfy the user's original Google Drive `08 — Прогнозирование движения цены` requirement of separating `PRESSURE` from `CONTROL` and demonstrating `REACTION`, `HOLDING POWER`, `Effort→Result` before assigning Control confidence 0–10. Draft #199 audits and forbids falsely promoting this baseline; Draft #200 adds a provisional six-state Market State classifier without changing Forecast V1.

This Draft adds a **separate opt-in research-only Control/Holding evaluator** `src/birzha/application/control_holding.py`; it does NOT modify an issued Forecast, Forecast Engine V1, the existing `market.state` vector or the G2 machine release gate.

## Exact causal implementation

`control_holding_from_snapshot(snapshot, instrument, series)` accepts a **single matching frozen MarketSnapshot**, exact instrument and a directly supplied source M15 `CandleSeries`. A verified, complete market-data collection must have occurred before invocation; this module does NOT fetch data and does NOT independently authenticate the supplied provider or timestamps.

- Need M15 >=50 completed bars and exact snapshot M15 count, quality-contract status PASS, valid positive ATR unit from snapshot, declared CANDLES and VOLUME capabilities, exact instrument SECID/board/engine/market/symbol. Every candle must have positive finite OHLC and volume, correct OHLC ordering, chronologically ordered nonoverlapping begin/end, exact or documented? **EXACT required**, plus available_at and observed_at between its completion and snapshot T0; any partial/future/incomplete or mismatch is **ABSTAIN**.
- Compute candidate `support=min(low)` and `resistance=max(high)` from the **20 fully completed candles before** the last 3 candles only. No retrospective level from the reaction/hold window, no future bars.
- Last 3 candles represent `REACTION` plus **two subsequent HOLDING** confirmations. Buyers: reaction touches support within 0.15 ATR and does not exceed 0.5 ATR false-break limit; reaction closes >=0.25 ATR above support; both future (but **already observed before T0**) hold candles close >=0.25 ATR above support and do not break it by more than 0.15 ATR. Sellers: exact inverse at resistance. If no reaction/holds or both sides appear plausible, ABSTAIN.
- A candidate score out of 10 is the explicitly proposed research heuristic: reaction 2–4 based on favorable ATR displacement, two holds 4, unsigned activity 0–2 from reaction-candle volume / pre-reaction 20-bar volume median. These values are **not probabilities, not calibrated, not an approved Protocol08 confidence formula and do not authorize trading**.
- Independently report the snapshot's `PRESSURE` from the matching complete `MarketFlowSnapshot` Delta ratio only if exact SECID/symbol/T0, capability TRADESTATS, PASS flow quality and no warning, valid -1..1 signed Delta. The pressure side can be **opposite** to detected holding (e.g., sell pressure absorbed while support is retained), demonstrating Control is not derived from Delta sign. If flow missing/degraded, report pressure UNAVAILABLE, not zero or fabricated.
- The output includes `PROVISIONAL_HOLDING_CANDIDATE` or `ABSTAIN`, frozen snapshot id, explicit reaction time and two hold times, preceding 20-bar levels, activity provenance, separate pressure, reason codes. The release-related flags are fixed to **false**: `provider_source_independently_authenticated`, `holding_confidence_externally_calibrated`, `alignment_or_route_approved`, `forecast_or_execution_authorized`, `production_authorized`.

### Material limitations

The control candidate uses candle-based volume context; **aggregate volume is not signed tape Delta or evidence of individual institutional intent**. The two-hold rule and reaction/volume points are an engineering *hypothesis*, NOT independently statistically validated holding. The level is a simple rolling price extreme, not a proven multi-test POC/VAL/VAH/HVN/LVN volume-profile level with role/strength/history. No event-time source attestation or approved exchange calendar/TSA is gained merely because candle fields claim EXACT; this module must remain separate from production decision paths until independently reviewed. It does not infer manipulation, order-book state, orders or an investable strategy.

Next legitimate engineering steps: evaluate historical **development-only** examples for failure modes, independently code-source-bound actual candle reading from receipts, level role/strength/history and obstacle-aware Route, then obtain user-approved 15-point Alignment scoring rules before Engine 3. Prospective quality, 5/10/20 outcomes, protected OOS and external trust require separate formal acceptance.

**No actual MOEX requests, external TSA, trades, main merge, HOME changes or production DB writes. The 12 G2 release blockers remain.**
