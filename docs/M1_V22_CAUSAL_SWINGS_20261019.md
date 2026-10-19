# M1 v2.2 — causal OHLC swing candidates (2026-10-19)

Canonical instruction: [Project Map → «Модель 1 — инструкция», rev 2.2, §§04–08](https://docs.google.com/spreadsheets/d/1_NGm6oiBF7rUV7TVNjP13huYyK4u8b21oKDOAQThEvQ/edit#gid=2026101801).

First standalone **computational primitive** after design-only Draft PRs #265–#266. This is not a Market State classifier, anchor selector, Control engine, completed parameter manifest or forecast.

## Algorithm

- Input is a bounded **exact SECID, exact timeframe** sequence of unmodified real or synthetic OHLC records. The caller must supply `swing_w` and `tie_break=EARLIEST|LATEST` explicitly. There are **no numerical defaults**.
- Preflight validates aware time intervals, chronological non-overlap, exact contract, finite OHLC envelopes, boolean completion and source-time shape. Invalid data cannot be silently resampled or replaced.
- At bar index `i`, use `bars[i-w : i+w+1]` only if every observed bar in that window is complete, `end<=T0`, `available_at<=T0` and has a source reference. Missing/late/right-side bars **block** that window instead of being dropped and compressing time.
- High swing requires the center to be the unique selected index of the highest high; low swing similarly for the lowest low. Equal extrema are disambiguated deterministically by explicit earliest/latest selection. An entirely flat OHLC window yields neither.
- Three distinct clocks: `event_start_time/event_end_time` are the interval containing the extremum; `event_time` is explicitly labeled **BAR_END_PROXY**, not the unknown exact tick time; `confirmed_at` is right window close; `available_at` is the latest input first-available time in that window.
- Output preserves exact input source refs, explicit swing parameters and T0; `calendar_attested=False`, `source_receipts_attested=False`, `forecast_allowed=False`.

## Testable design invariants

- No right window, no confirmed swing.
- Adding future candles cannot change prior T0 swings; a late receipt cannot be retroactively known.
- A partial candle cannot be treated as a complete bar; missing data cannot be skipped to manufacture an adjacent pivot.
- The same tie rule and input produce the same result; equal-extreme ties and flat series have explicit behavior.
- Mixed futures SECIDs, invalid OHLC and malformed timestamps are errors, not trading signals.
- Nothing in this module consults ATR, ER, OOS or unapproved thresholds. The `swing_w` must later be provided by a separately frozen and approved parameter manifest.

## Research-only limitations

1. No independently attested session calendar or proof of missing intervals. A physically absent candle cannot be discovered by this small pure function. A future source-bound data admission layer must validate calendar, gaps and source records **before** accepting pivots into an operational engine.
2. No synthetic volume, intrabar pivot timestamp or direction-control interpretation.
3. A pivot is not a trend change, breakout anchor, retest or level strength without later independent causal evidence.
4. No integration with `forecast.build/create`, journal, MCP registry, HOME, installed skill, or database.

Test command for exact PR head in a Python 3.12+ checkout:

```sh
python -m pytest -q tests/test_m1_causal_swings.py tests/test_m1_mandatory_chain.py tests/test_m1_research_governance.py tests/test_m1_parameter_manifest.py
```

**Next bounded step:** independent review of this raw-bar contract, then consolidation/breakout-anchor candidate from verified local levels and predeclared parameters. Before release, integrate with the real data admission and full frozen manifest, and demonstrate causal replay across exact-contract session series with authorized data.
