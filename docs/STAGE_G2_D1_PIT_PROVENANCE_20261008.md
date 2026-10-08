# Stage G2 D1 point-in-time provenance — 2026-10-08

Scope: new research-only, read-only evidence inventory. This is NOT a classifier, forecast, or strict historical causal replay.

## Findings

- The transferred 18-column archival DuckDB lacks historical available_at, available_at_confidence, observed_at, revision and historical_candle_revisions. Earlier 06.10 read-only audit confirmed this fact; the archive SHA-256 is 8dcd4bbf560d1123575403b0c63d0c21891c4d152abbadc7a3bf9434ccbe68e1.
- V2 exact-SECID reconstructed warmup report, independently reviewed 08.10, supports 4,955 / 4,955 reconstructed eligible D1 windows in BR/Si/GOLD across 133 contracts. It is not as-known-at-T0.
- In main, snapshot.py:_cut_at uses completed + candle.end + available_at, but not observed_at or a vintage selector. Its fallback to next-day midnight for D1 is an inference, not a historical receipt record.
- market_data.py:_normalize_completion emits INFERRED availability for D1 with no source publication timestamp. historical_store.py:read returns a stored base version without a historical observed-at selector.
- features.py drops missing close/OHLC values before several calculations. Only previously checked exact-session, gap-free 21-bar windows are suitable research feature inputs; a compressed subset is not.
- Root contract selection can depend on whole-session value/volume, unsuitable for selection within that session without delayed knowledge-time T0.
- 06.10 prior audit documented a legacy update path where an old NULL revision and changed incoming payload may cause revision hash/price mismatch. That is a separate engineering defect, not repaired here.
- 2021–2022 was research-exposed, 2025–May 2026 SBER/Si performance-exposed; 2023–2024 M23 holdout reservation remains UNKNOWN consumption. No independent historical holdout established.

## Safe inventory tool

Run: python -m scripts.d1_pit_provenance_inventory --archive <path> --v2 <path-to-V2-JSON>.

Reads raw DuckDB via duckdb.connect(..., read_only=True), never instantiates the schema-changing historical store. Hashes source before and after; reports raw D1 FUTURES bar counts by root, excludes equity SECID GOLD, inventories temporal columns and revisions table. The optional V2 report must match the archive SHA-256 and explicit reconstructed status.

Output STRICT historical T0 is always NOT_PROVEN. Even modern schema columns are not sufficient without actual first-receipt/version evidence and a decision knowledge cutoff. The tool cannot silently promote any data.

## Next gates

1. Run the inventory against the immutable archive and preserved V2 report; verify the report and archive SHA.
2. Review separate event time and decision knowledge cutoff contract for prospective collection; do not patch snapshot T0 one line at a time.
3. After frozen definitions and Development ledger, develop exact-contract D1 research features and temporal folds. No threshold/model/OOS study on reserved holdout before independently confirming its status.
4. No merge, deployment, HOME or production DB modifications.
