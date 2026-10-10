# Stage B — version-content SHA integrity + causal corruption isolation (10 October 2026)

**Owner:** ChatGPT independent data-fix line, based on forecast/prospective HEAD #148. No files on Codex-colleague TREND/BALANCE branch are edited.

## Confirmed defect from code review

The versioned reader `DuckDBHistoricalCandleStore._selected_candles` reconstructed each `historical_candle_revisions.payload_json` and checked `begin` and `source`, but **did not verify the stored SHA-256 revision against the actual serialized OHLC/source payload**. A damaged or modified revision row with unchanged metadata could silently change an as-of or latest close. A first attempted fix also exposed an important representation difference: the base OHLC table does not preserve original JSON numeric types (100 vs 100.0). An integrity check that blindly reserializes base floats falsely rejects perfectly valid original integer-valued candles; this is why the new SHA check is **revision-table only**. Worse, `read_as_of(T0)` parsed **future-received revisions** before filtering by their observed_at, allowing future corruption to break a historically earlier read.

Both are data-integrity failures and relevant to the roadmap's Stage B / G2-DATA-FIXES. They are distinct from the old known limitation: missing original first-seen timestamps cannot be recreated from a later archive.

## Limited code change

- **Content-addressed revision guard:** verify SHA-256 directly from the **original stored revision payload_json bytes**, where their exact serialization is retained. A hash mismatch raises an explicit error; no altered correction is returned. The base table has no original JSON bytes, so a reconstructed Candle cannot be rehashed reliably after DuckDB numeric coercion.
- Historical `knowledge_cutoff` additionally **refuses an opaque vendor-provided revision token** as strict SHA evidence. The existing `read()` first-seen semantics are untouched; `read_latest()` remains backward-compatible for opaque legacy tokens as an unverified *current-mode* projection. This is intentionally not a backward-compatibility change to immutable legacy readers.
- Filter revisions by *observed_at before parsing payload JSON* when an explicit T0 is requested. A malformed version first observed tomorrow cannot spoil a historical snapshot at today's cutoff. Later corruption will raise only when it is eligible to be considered at that later T0. When that later T0 is reached the bad row fails normally.

No ALTER TABLE, no migrate, no old-payload rewrite, no production DB or HOME access, no changes to Forecast Engine or existing Prediction/Outcome thresholds.

## Negative tests

`tests/test_g2_revision_content_integrity.py` only uses in-memory DuckDB and checks:

1. Original close=100 at early T0, corrected close=110 later; mutation of stored revision payload to 999 causes **fail closed** at the later cutoff and latest view, but earlier T0 remains unchanged.
2. A future-received revision with malformed JSON never affects an earlier T0; reading after its known arrival raises.
3. Original integer-valued source JSON safely survives DuckDB DOUBLE readback without a false SHA mismatch; this explicitly does NOT prove the immutable base row's original raw bytes.
4. Opaque revision tokens stay supported in explicit non-strict first/latest modes, **never** certified as content-hash-based historical evidence.
5. Legitimate 100→110 completion/correction retains the intended versioned behavior.

## Unresolved prerequisites

This change secures stored-content integrity, not truthful provider publication time. Input `observed_at` can still come from a calling source without third-party timestamp attestation. Historical exchange routing/metadata vintage and publication-time provenance remain NOT_PROVEN; old unknown receipts remain excluded from historical strict replay. An independent human code review and explicit user permission precede any merge, HOME deployment or production DB operation. The current forecast/OOS model-quality claims remain unvalidated.

**Acceptance:** all five targeted negative/positive cases, existing stored_data/read/snapshot causal regression suite, and full project pytest PASS in GitHub CI; independent peer review pending.

## Explicit residual base-row trust boundary

**Not solved:** the pre-existing immutable base candle table stores typed numeric columns but not the originally serialized payload_json bytes. Its `revision` field may have been computed from integer input while DuckDB later yields float values, so independent byte-level SHA validation of base rows is **not possible without an originally saved raw payload**. The original base writer and immutable storage invariants therefore remain trust assumptions; a corrupt base DB row cannot be deemed SHA-verified by this new test. Do not relabel an old archive as strict historical point-in-time or claim base-row tamper detection. A future versioned raw-payload ledger/outbox and immutable external anchor are a separate design/permission gate, not a silent migration of existing production files.
