# G2-DATA-FIXES — Finality projection & historical observed-as-of reader (09.10.2026)

Independent draft stacked above PR #133 → PR #132. No merge/deploy/production data mutation.

## Exact issue

The storage writer intentionally kept the immutable first payload for every (SECID,timeframe,begin), while later payloads were append-only in `historical_candle_revisions`. However `read()`, `coverage()` and `stored_trade_dates()` ignored those revisions. Hence forming→final was persisted but not reflected in analysis/readiness; a later correction might be ignored by the reader.

## Implemented scope

- `read()` retains first-seen immutability, so callers/archival validation are unchanged.
- `read_latest()`: explicitly **latest projected content** (not strict historical T0). Select the latest observed **completed** version whenever any completed version exists; later stale forming revisions cannot downgrade it. A later finalized correction can supersede a previous completed version. The earliest base record remains immutable.
- `coverage_latest()` and `stored_trade_dates_latest()` reflect the same selected projection if revisions exist. Otherwise existing fast SQL remains unchanged.
- `read_as_of(...,knowledge_cutoff=<timezone-aware ISO>)`: explicit opt-in selection from known first observed versions up to the supplied decision time; incomplete bars may be returned as incomplete, but completed bars must have event end and any version-specific available_at no later than cutoff. Legacy base rows lacking observed_at are rejected rather than assigned an invented first-receipt. Same-timestamp conflicting versions fail closed.
- No migration of original OHLC, no price-change suppressions and no invented historical timestamps. Version row available_at is UNKNOWN, not copied from unrelated first base metadata.
- All reports of 4955 reconstructed D1 windows remain *research-only*, not historical as-known-at-T0. `StoredMarketDataView` is NOT YET wired to the new `read_as_of`: existing historical validation must not be claimed strict replay on the basis of this PR.

## Focused tests

- forming→completed→current read/coverage/trading dates and version base immutability;
- cutoff before/after finalized receipt;
- stale later forming update cannot supersede final;
- newer corrected finalized payload appears only after its observation time;
- old archive with unknown first receipt fails closed under as-of;
- timezone-naive cutoff is rejected;
- completed candle may not appear before its event end;
- conflicting same-timestamp versions fail closed.

**Follow-up:** assess performance for large revised archives, wire explicit knowledge cutoff through the historical stored-data view and fixed T0 choice, then independently review before merging. Do not use older reconstructed prices as strict PIT evidence.

### CI correction: 09.10.2026
Initial combined CI failed: an existing immutable `read()` regression expected original close 101 but silently changing `read()` returned revised close 999. Corrected by keeping `read()/coverage()/stored_trade_dates()` unchanged and introducing explicit `read_latest()/coverage_latest()/stored_trade_dates_latest()` + `read_as_of()`. This is a contract breach caught by CI, not evidence of forecast quality. An opt-in caller integration is required before current stored views use the latest projection.

## Explicit StoredMarketDataView integration

`StoredMarketDataView.version_view` defaults to `first_seen` (all legacy callers unchanged). `version_view='latest'` explicitly uses DuckDB `read_latest` for current/reconstructed use; `version_view='as_of'` requires the caller to pass a **timezone-aware** `now` knowledge cutoff and uses `read_as_of`. Backends without the corresponding reader fail closed. `completed_only=True` remains a separate filter: an old forming bar cannot masquerade as a final bar before its actual receipt. Historical snapshot builder does not yet propagate a single decision-time cutoff across candles, contract selection and flow, so this is not a blanket strict causality PASS.
