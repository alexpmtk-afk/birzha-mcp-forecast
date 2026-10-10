# G2 D1 formula contract — research-only, 10.10.2026

## Decision based on existing G2 source documents (06.10)

This implementation **follows, and does not overwrite**, the existing documented numerical definition:
- `docs` independent source: Google Drive `D1_CAUSAL_PROVENANCE_AND_FEATURE_CONTRACT_20261006.md` and `G2_D1_RESEARCH_DESIGN_REVIEW_20261006.md`.
- `ATR14_t = mean(TR[t-13] ... TR[t])` (arithmetic SMA of fourteen true ranges; 15 OHLC candles), **not Wilder/RMA**. The roadmap phrase "ATR Wilder/SMA" calls for choosing/versioning, not silently changing the existing Forecast Engine.
- `D5_t = (C[t] - C[t-5])/ATR14_t` and `D20_t=(C[t]-C[t-20])/ATR14_t`: direct price displacement at current volatility scale. The old normalized `d1_return_20_atr` is **different**, remains untouched and is not reinterpreted.
- `ER20_t = abs(C[t]-C[t-20])/sum(abs(C[i]-C[i-1]), i=t-19..t)`. A zero denominator with 21 valid candles is explicitly UNAVAILABLE / zero_20_step_closing_path, not arbitrary 0 or false TREND.
- `W20_t=(max(H[t-19:t])-min(L[t-19:t]))/ATR14_t`. Exactly twenty candles for the range, 21 for ER/D20, fifty for SMA50.
- `SMA20` and `SMA50` are simple mean of latest 20/50 complete closing values; SMA50 cannot be proven from a 21-bar input.
- Volume ratio20 is computed only when instrument declares VOLUME and 21 volume observations are finite/nonnegative with positive baseline. Index volume=0 with no VOLUME capability is NOT_APPLICABLE, not a missing OHLC or an exclusion.
- OI/Tradestats session VWAP/volume profile remain NOT_APPLICABLE if capability missing, or UNAVAILABLE when supported but not supplied by this D1-only source; zeros are never fabricated.

## Independent admission boundary

The pure `build_d1_research_features` requires exact SECID, explicit strictly increasing ISO expected sessions, equal actual candle dates and explicit evidence origin/key. **The caller must independently validate the actual exchange session calendar and provenance/key against source/manifest**; accepting a caller string is *not* a historical T0 proof. All output explicitly states `RECONSTRUCTED_RESEARCH_ONLY`, strict historical as-known-at-T0 FALSE.

No silent gap compression, cross-contract concatenation, incomplete bars, missing/nonfinite OHLC, invented zeros, or universal thresholds. Price integrity checks require positive finite OHLC and high/low containment. Zero ATR means ATR-normalized price features unavailable. No classification, fit, holdout/OOS read or production feature/forecast identity modifications.

## Version and future dataset

`G2_D1_RESEARCH_SMA_TR14_D20_ER20_W20_V1` labels the pinned formula and statuses. A future dataset must hash (a) exact price archive (b) exact SECID/expected calendar/evidence origin/key (c) this formula version (d) code/selection policy and (e) output artifact. Merely hashing price-only input is not enough.

This is a bounded first G2-DATA-FIXES formula subgate, to be followed promptly by G2-DATA-ADMISSION, the six-market D1 research dataset and first TREND/BALANCE hypothesis experiment. Do not merge or deploy, preserve old immutable Snapshot/ForecastRecord IDs.
