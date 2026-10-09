# G2-DATA-FIXES: targeted legacy revision / payload integrity

Scope: isolated fix based on draft compatibility line PR #132; does not change forecast model or deploy schema.

Problem reproduced in 06.10 independent audit: legacy base close=100, revision=NULL. Later close=110 is ingested; old ON CONFLICT left price 100 but populated revision SHA from 110 and current observed_at. This violated stored first-version identity and falsely suggested old receipt.

Fix: while under per-store upsert lock, if an existing row has revision NULL, compute its content fingerprint from the ACTUAL persisted OHLC/source/end/completed values and stamp only that hash. Then existing append-only revision insert detects a different newly fetched version and records it in historical_candle_revisions, leaving first base price unchanged. Existing first receipt timestamp for a base row is never overwritten with a later backfill timestamp; null remains explicitly UNKNOWN.

Tests (synthetic in-memory DuckDB): legacy 100->110 creates base-hash(100) and separately incoming-hash(110) while retaining observed_at=NULL; same-content reingest creates no second revision; modern first-seen row stays immutable when incoming content differs.

Limits: historical provenance still not recovered, old rows with supplied non-content-hash revision contract require independent interpretation, finality/incomplete->complete and cross-session knowledge-time T0 remain separate G2-DATA-FIXES. No authorization to merge, deploy or alter production archive.
