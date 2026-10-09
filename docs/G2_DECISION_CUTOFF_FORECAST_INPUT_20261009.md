# G2-DATA-FIXES — explicit decision cutoff in forecast input (09.10.2026)

## What is implemented

An **opt-in** `knowledge_cutoff_at` parameter is propagated from `ForecastService.build` and `MarketSnapshotService.build` / `build_with_instrument` to ALL D1, H1, M15 `StoredMarketDataView.version_view='as_of'` queries, including the M15 fallback. Snapshot and forecast `as_of`/`created_at_t0` are explicitly that decision instant, not a max candle end synthesized after the fact.

Historical receipt cutoff requires:
- timezone-aware ISO decision timestamp in Europe/Moscow comparison;
- `StoredMarketDataView` with `version_view='as_of'`, `require_stored_resolution=True`, exact stored SECID, no root-futures auto selection;
- `flow=None` because historical TradeStats/FUTOI/public trades presently filter event timestamps but lack proven receipt-vintage histories;
- `read_as_of` excludes base OHLC lacking first observed timestamp and all later-arriving versions; further candle end + available_at checks apply.
- `as_of_date`, if supplied, must match decision date.

**No change to default `WalkForwardValidator.run`, `MarketSnapshotService.build`, `ForecastService.build` or existing record schemas.** Without an explicit cutoff, current baseline code path remains unchanged. Old reconstructed V2 warmup evidence cannot be promoted to historical T0 truth. Exact stored instrument metadata is not independently attested to have existed on historical T0, so this is a bounded **price-receipt filter**, not full strict point-in-time forecast certification.

## Scope tested

Synthetic complete previous-session OHLC for one exact SECID across D1/H1/M15, independently corrected later; compare snapshot and ForecastRecord at 09:00 and 11:00. All early prices remain 100, later prices 110. Root `BR` rejected, missing SECID, fallback/latest view, live market data, optional flow rejected. No backtesting/OOS accuracy claim.

## Remaining

Independent review of resolver provenance and formula capability, future exact contract ID as-known-at-T0; prevent any later historical source/volume/flow at old T0; separate historical validation opt-in after real prospective receipts. Keep draft PR, no merge, production DB mutations or deploy.
