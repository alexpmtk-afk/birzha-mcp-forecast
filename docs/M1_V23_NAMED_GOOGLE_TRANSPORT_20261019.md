# M1 v2.3 named-field Google transport — isolated acceptance

Depends on [Draft #271](https://github.com/alexpmtk-afk/birzha-mcp-forecast/pull/271), not baseline `main`. Canonical specification: Google Project Map / «Модель 1 — инструкция» v2.3. This is a tested, callable candidate adapter and **not** a deployed or registered write path.

## What it does

`M1NamedGoogleSheets` uses the existing `NativeCanonicalSheets._request` authenticated HTTP transport (Google Drive identity and Google Sheets metadata/values/batchUpdate API) but **not** its 40-column `validate_target`/`read_rows` helper. The six current canonical indicator tabs use **54 columns**; the legacy native transport has `MAX_COLUMNS=40` and cannot simply be expanded globally without re-validating established mirror contracts.

The candidate reads the exact spreadsheet ID, expected file title, sheet identity, grid bounds and *live header row*. It maps names to current positions via `resolve_header`, reads bounded 2,000-row chunks, and returns named records. It has no positional assumptions about `formula_id`, `source_record_key`, `atr_14_wilder` or legacy fields. Formula values are read with `valueRenderOption=FORMULA`, so a formula is not mistaken for a literal calculation.

**Mutation is limited to a verified noncanonical test copy**: an explicit `isolated_copy_approved=True` is required, every pinned canonical workbook ID is refused, existing keys are required, only the four named status fields `indicator_status`, `atr_14_wilder_status`, `quality_status`, `calendar_status` may be patched, only by safe string literals. No formulas, price values, added rows, user-authored metadata, contract identities or historical forecast data can be mutated. BatchUpdate addresses are resolved from the actual live header, and physical row indices are preserved even if there are blank physical grid rows.

Before mutation, the complete snapshot is reread and compared. After mutation, the complete named field contents of every populated row (including untouched fields) are compared to the expected poststate, with duplicate/absent keys rejected. A lost API acknowledgment or partial update is a **hard failure with no automatic retry**, not presumed success. Without native Sheets conditional mutations, there remains a TOCTOU window between last read and the write; an authorized exclusive test copy is essential and this helper does not claim transactional atomicity across independent writers.

The module contains no runtime invocation, MCP registration, install/deploy, credential creation, scheduling or HOME action. The deployed shared mirror/derived-writer use cases remain separate until independently approved. A future runtime integration must choose explicit version/schema policy for legacy 34-column derived tabs vs the current 54-column M1 tabs and must preserve immutable history.

## Verification

`tests/test_m1_named_google_sheets.py` exercises the **real HTTP request shapes** end-to-end against `httpx.MockTransport`: identities, exact header mapping, reordered columns, duplicate headers, missing destination keys, no auto-created rows, canonical write rejection, formula overwrite refusal, partial/lost-response detection and physical blank rows. No live spreadsheet is modified during tests. Exact commit and GitHub CI are required for acceptance.

**Not proved by CI**: the dynamic ATR formula recomputes when OHLC changes, full independent 237,665-value ATR replay, source first-availability/calendar attestation, identity or data retention across production changes, M1 numeric parameter_manifest, real forecasts or outcome precision. No merge, deploy or release without separate authorization.
