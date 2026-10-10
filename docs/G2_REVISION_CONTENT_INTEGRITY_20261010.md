# Stage B — version-content SHA integrity + causal corruption isolation (10 October 2026)

**Owner:** ChatGPT independent data-fix line, based on forecast/prospective HEAD #148. No files on Codex-colleague TREND/BALANCE branch are edited.

## Confirmed defect from code review

The versioned reader `DuckDBHistoricalCandleStore._selected_candles` reconstructed each `historical_candle_revisions.payload_json` and checked `begin` and `source`, but **did not verify the stored SHA-256 revision against the actual serialized OHLC/source payload**. A damaged or modified revision row with unchanged metadata could silently change an as-of or latest close. Worse, `read_as_of(T0)` parsed **future-received revisions** before filtering by their observed_at, allowing future corruption to break a historically earlier read.

Both are data-integrity failures and relevant to the roadmap's Stage B / G2-DATA-FIXES. They are distinct from the old known limitation: missing original first-seen timestamps cannot be recreated from a later archive.

## Limited code change

- **Content-addressed version guard** on `_selected_candles` candidates: recompute using existing `_candle_revision` and require exact equality for SHA256-shaped revision tokens. If the payload does not match stored digest, raise a precise error; never return altered content.
- Historical `knowledge_cutoff` additionally **refuses an opaque vendor-provided revision token** as strict SHA evidence. The existing `read()` first-seen semantics are untouched; `read_latest()` remains backward-compatible for opaque legacy tokens as an unverified *current-mode* projection. This is intentionally not a backward-compatibility change to immutable legacy readers.
- Filter revisions by *observed_at before parsing payload JSON* when an explicit T0 is requested. A malformed version first observed tomorrow cannot spoil a historical snapshot at today's cutoff. When that later T0 is reached the bad row fails normally.

No ALTER TABLE, no migrate, no old-payload rewrite, no production DB or HOME access, no changes to Forecast Engine or existing Prediction/Outcome thresholds.

## Negative tests

`tests/test_g2_revision_content_integrity.py` only uses in-memory DuckDB and checks:

1. Original close=100 at early T0, corrected close=110 later; mutation of stored revision payload to 999 causes **fail closed** at the later cutoff and latest view, but earlier T0 remains unchanged.
2. A future-received revision with malformed JSON never affects an earlier T0; reading after its known arrival raises.
3. A tampered content-addressed base row is refused under as-of.
4. Opaque revision tokens stay supported in explicit non-strict first/latest modes, **never** certified as content-hash-based historical evidence.
5. Legitimate 100→110 completion/correction retains the intended versioned behavior.

## Unresolved prerequisites

This change secures stored-content integrity, not truthful provider publication time. Input `observed_at` can still come from a calling source without third-party timestamp attestation. Historical exchange routing/metadata vintage and publication-time provenance remain NOT_PROVEN; old unknown receipts remain excluded from historical strict replay. An independent human code review and explicit user permission precede any merge, HOME deployment or production DB operation. The current forecast/OOS model-quality claims remain unvalidated.

**Acceptance:** all five targeted negative/positive cases, existing stored_data/read/snapshot causal regression suite, and full project pytest PASS in GitHub CI; independent peer review pending.
