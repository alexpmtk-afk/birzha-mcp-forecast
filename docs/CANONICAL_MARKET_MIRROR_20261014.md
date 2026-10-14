# Canonical market mirror correction — 2026-10-14

## Scope and evidence

Base: origin/main b6c085db18d1811f46077a447e6a2251b2df85f0.
Open PR titles/tips (49, 62–64 and 123–243) inspected; no competing mirror correction identified. This is not a complete review of every PR diff.
Existing canonical Drive file metadata and first H1 rows read independently on 14 October.
The six file IDs, names and exact parent folders match instruction 09 version 1.1.
HOME checkout reports the same main SHA; its google_sheets_bridge.py still has old names
and the 25,000-row/D1-only legacy contract. This is installed-file evidence, not a
claim about the code loaded in a running process. No process, configuration, secret,
production database or Google candle was changed.

## Implemented

- All six canonical spreadsheet IDs pinned; correct display names/folder labels.
- TATN has no registered canonical file and cannot trigger creation.
- Legacy path lookup removed from the six-market mapping. Responses claiming file
  creation, missing creation status, wrong ID or name are rejected.
- Legacy full replacement of any canonical workbook disabled BEFORE its first
  staging call. This includes SYNC_STATUS: replacing it would erase consolidated
  research provenance. The existing worker therefore cannot be activated from
  this branch until its mirror path is migrated; mandatory mirroring remains closed.
- New CanonicalMirrorPublisher / NativeCanonicalSheets implements append-only
  reconciliation for D1/H1/M15, no book/tab creation, no source requests or DB writes.
- Header equality checked; existing 20/26/28-column schemas are retained. Empty
  existing tabs can receive a validated source header and bars; nonempty schema
  changes require explicit migration, never silent column dropping.
- Exact contract/key/instrument, duplicate key, finite OHLCV, OHLC relations,
  literal cells, source flags and historical availability restrictions checked.
- New H1/M15 retain unknown session dates. INFERRED bar-end availability is
  accepted only with explicit PIT_NOT_ATTESTED caveat. Existing SBER/IMOEX/Si
  research labels retained; no promotion to verified historical availability.
- Overlapping identical keys skipped; conflicting key/value evidence rejects
  the entire plan before writing. No reinterpretation or price adjustment.
- Native appendCells writes explicit literal string/number/bool values. Only this
  mutation is exposed. Preflight and full readback detect destination changes.
- Every write chunk independently checked; PASS returned only after final full
  readback. Lost responses are not automatically retried. A fresh run reconciles
  committed keys and resumes missing ones. A partial run is NOT workbook-atomic.
- Existing up to 100,000 rows/40 columns supported, removing the 25,000-row legacy
  obstacle on the new path. At most 2,000 additions per operation, chunks <=1,000.
- Native requests paced at 1.1s; shared quota exhaustion refuses the operation.
  Errors expose neither access tokens nor raw response/exception bodies.

## Deliberate activation boundary

This is an opt-in publishing library, NOT a replacement already connected to the
scheduler or MCP. Input is approved literal table rows from the accepted source;
no archive paths, final HOME research location or trading-calendar evidence are
invented. Collection/resumable source adapters remain their existing owners' work.
No automatic credential discovery. An authorised caller supplies a token provider.
A single writer must own the ENTIRE destination workbook; the boolean API flag is
an explicit precondition, not a distributed lock. Google read-then-append is not a
compare-and-swap transaction. An external writer can race after preflight; therefore
concurrent writers are forbidden, and contention tests do not prove isolation.
The stable shared Apps Script implementation is absent from this repo; extending
its deployed protocol without source/acceptance would be unjustified. Native Google
API transport is separate and must undergo a controlled external acceptance before
any runtime substitution. Its authorisation requirements differ from Apps Script.
D1/SESSIONS/VERIFIED_RANGES/SYNC_STATUS worker migration remains BLOCKED for activation;
these service sheets were deliberately not rewritten under an unverified backend.

## Reviewer procedure

1. Review fixed IDs against instruction 09; confirm old Si Snapshot is provenance
   only and no active source code writes to it (searched src/ and scripts/).
2. Inspect the guard's effect on the legacy worker: rejection is intentional,
   not a silently working scheduler. Keep deployment blocked until migration.
3. Run the focused offline tests; test copied header/data schemas from actual
   SBER, Si and IMOEX including source partial flags and unknown fields.
4. Review native API request shapes, row bounds, empty-tab initialisation, negative
   errors, preservation of old rows, restart after lost response and ownership limit.
5. Separately establish running revision, current auth mechanism, one integrator,
   approved read-only source adapter/path and service-sheet update semantics.
6. Authorise acceptance on a disposable noncanonical test workbook first. The
   production transport has no configurable destination bypass; use a dedicated
   test-only harness, never weaken production ID checks to make a test pass.
7. Only after independent acceptance and a separate install decision migrate the
   runtime worker, record revision/configuration, safely verify all six destinations.

NTP was not implicated by the source report; no clock changes are warranted.
Empty GOLD/RTSI intraday, data freshness, calendar and PIT attribution remain
separate collection/attestation tasks, not rename defects.

## Official API references

- https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/request#AppendCellsRequest
- https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets.values/get
- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get
- https://developers.google.com/workspace/sheets/api/limits

This document describes repository implementation and its acceptance boundary;
canonical Google instruction remains at its existing Drive URL, not copied into Git.
## Local checks

- Final focused/regression suite: 83 passed in 2.84s.
- Actual native H1 header/two-row samples from Si (28 columns), IMOEX and SBER
  (26 columns) passed offline no-op reconciliation. This is sampled schema
  compatibility, not a new complete price audit or live write acceptance.
- Full suite: 401 passed, 1 failed (98.60s). Failure is unchanged
  test_daily_tick_drains_oldest_archive_before_starting_newer_range.
- The exact main was independently extracted by git archive and that test run
  there: same failure (1.46s). It is not caused by the mirror change. Full suite
  is therefore PARTIAL, not claimed PASS; the baseline queue-order defect remains
  outside this bounded correction and must be checked against later coordination PRs.
- Initial full-suite collection needed Windows-local temporary journal/history
  paths instead of default /tmp; no production path was used.
- No live native API write, scheduler activation, HOME install, MOEX download,
  merge or clock operation. Offline transport tests use httpx.MockTransport only.
