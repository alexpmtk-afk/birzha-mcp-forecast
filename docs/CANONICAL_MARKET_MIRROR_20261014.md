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

## Integrated continuation — 2026-10-14

This section supersedes the earlier statement that the publisher is not wired to
MCP or a HOME refresh scenario. The code is now integrated; runtime activation
is still separate and has not been performed.

Observed actual HOME runtime:
- MCP runs from C:/MCP-HOME/projects/birzha-mcp-forecast at the old main SHA.
- start-birzha-home.ps1 sets DuckDB and source/history paths.
- Its bridge-config.json does not exist; Google mirroring is not configured.
- Active hourly run-history-refresh.ps1 updates D1 (120d), H1 (95d), M15 (35d)
  through existing authenticated history.sync_batch. Its 14 Oct 18:44 cycle passed
  all six markets/timeframes, but it does NOT export them to Google.
- The active refresh script is in the user's existing BirzhaControl directory;
  it is distinct from the legacy YDB cloud worker. Neither process was stopped.

Implemented continuation:
- DuckDBHistoricalCandleStore.export_mirror_bundle takes a store mutex and one
  read transaction on the existing process connection. No second DuckDB writer,
  database copy, source request, DDL, calendar inference, or source-price update.
- HomeCanonicalMirror preflights all three candle sheets and all service schemas
  before mutation, projects stored data to actual destination columns and preserves
  accepted rows when core prices/volumes/intervals/source-completion match.
- Semantic timestamp aliases (naive provider clock vs explicit Moscow offset)
  match by exact SECID and instant; older record keys/provenance remain untouched.
  The provider timezone interpretation is explicit and is NOT a PIT attestation.
  Different source provenance is reported separately, not called a full payload match.
- Source missing values, conflicting prices/intervals, duplicate semantic identities
  or unknown column projection block the operation before price writes.
- SESSIONS and VERIFIED_RANGES append existing stored evidence; range scope
  retains original marker identity and explicitly denies independent attestation.
- SYNC_STATUS receives an in-progress marker before data writes, failure marker
  on partial failure, and PASS only after final candle and service readbacks.
  Old configuration values are preserved under legacy_pre_mirror_v3 keys.
- mirror.status reads activation settings without secrets/network. mirror.sync
  updates one workbook and mirror.sync_core optionally sequences the six. Dry-run
  is default; disabled/missing configuration returns BLOCKED, not false PASS.
- GoogleRefreshToken uses only the explicit credential file configured by an
  authorised owner. OAuth refresh and safe token caching implemented, no discovery.
- Settings require explicit native activation, DuckDB, authenticated MCP, source
  SHA, credential path and sole-writer declaration; simultaneous legacy/native
  writer configuration refused.
- scripts/run_home_history_and_mirror.ps1 is based on the real refresh scenario:
  source success and mirror success are logged separately. Each mirror is called
  only after source refresh; whole-cycle PASS needs all six mirrored books.
- scripts/start_home_with_native_mirror.ps1 keeps the existing startup/security
  scheme and adds explicit native configuration. Source SHA is read from the
  installed Git revision rather than the former hardcoded old main SHA.
- scripts/authorize_home_google_mirror.py prepares desktop OAuth with PKCE/state,
  loopback callback and explicit browser consent; no password entry or token output.
  Credential output is exclusive and must be in an owner-restricted LOCAL folder,
  never in the shared Drive workspace. Existing secret files were not read.

Checks:
102 focused/regression tests passed (16.61s), including exact Si/BR/GD contracts,
versioned futures session keys, read-only source, all candle/service sheets,
partial failure/resume, alias provenance preservation, status updates and MCP.
Full local continuation suite before the final 5 tests: 417 passed and same known
baseline queue-order test failed (107.12s). Full local PASS is not claimed.
PowerShell startup and refresh scenarios parse successfully; Python compiles.
Native Google appendCells/updateCells shapes exercised through connected Google
in a separate copied test book, not any canonical destination:
https://docs.google.com/spreadsheets/d/11eJhW7UnGRNUV9xlIss4wPadxohBqHfzjkP1ygImJu4/edit
QA_CANDLES preserves 100.25, false, UNKNOWN, then appends 101.5, true, UNKNOWN.
QA_STATUS preserves legacy_evidence=PRESERVE while updating status in place.
This live API shape acceptance is NOT live HOME OAuth/connection acceptance.

Activation procedure (concrete remaining gate):
1. Create desktop application credentials in the owner's Google Cloud project;
   enable Sheets and Drive APIs. Owner must accept Google consent once. Google
   OAuth scopes are spreadsheets and drive.metadata.readonly; the program further
   restricts writes to the six pinned books. Google account passwords are never
   requested by the program. A testing-mode OAuth consent setup may expire its
   refresh token; stable unattended use needs an appropriate consent configuration.
2. Run authorize_home_google_mirror.py with the downloaded desktop client file and
   an access-restricted local credential destination. No credentials in chat/Git/Drive.
3. After independent code acceptance and an explicit installation decision, install
   from main through the existing HOME control. Do not deploy a Draft head directly.
4. Set C:/MCP-HOME/runtime/birzha/native-mirror-config.json with enabled=true,
   sole_writer=true and the authorised credentials_path. Confirm other writers
   have stopped for these books; the flag is not a distributed lock.
5. Back up and replace only the two Birzha startup/refresh scripts with the reviewed
   versions. Restart only the Birzha runtime and its refresh loop via existing control;
   no computer restart and no unrelated process operation. Separate approval applies.
6. Verify mirror.status revision/settings, then mirror.sync dry_run=true for each
   market. Any conflict remains a blocker; it is not resolved by overwriting history.
7. Perform one explicitly approved real mirror cycle, prove full readbacks and
   final status, and observe a later scheduled cycle. Only then claim automatic
   mirroring accepted. On failure restore previous scripts/config/revision; source
   history and Forecast/Outcome are not rewritten by rollback.

Official desktop OAuth reference:
https://developers.google.com/identity/protocols/oauth2/native-app

No source download was manually started. Any standard MOEX workflow automatically
triggered by updating this PR is separately reported in the PR's acceptance log.
Incremental scheduling optimisation: source rows are reconciled once. Only missing
keys enter write batches; a no-change frame uses one verification pass, rather than
replaying every already mirrored 120-day source slice. Full final readback remains.


## Service account continuation — 2026-10-14

The owner created the Birzha Market Mirror project, enabled Sheets and Drive APIs,
and created a service account after the personal OAuth publication path was blocked
by missing homepage/privacy URLs. No website is required for the selected service
account path. The downloaded desktop OAuth client is not the active credential.

GoogleRefreshToken now accepts explicitly configured service_account JSON as well
as the earlier refresh-token format. The service account grant uses Google's
library, a fixed Google token endpoint, only Sheets and Drive metadata read scopes,
no delegated user subject, and a signed one-hour assertion. No remote endpoint
from an untrusted credential file can receive the grant. Credentials remain lazy
loaded, token cached, errors redacted, redirects and automatic retries disabled.
The official google-auth dependency is pinned to 2.62.0.

25 auth and HOME mirror regression tests passed (15.75 seconds); seven new cases
use generated synthetic keys only, including signature/scopes/expiry/cache and
rejection before network for foreign endpoint, universe, delegation, bad identity,
bad private key and external_account input.

An owner-authorised real token exchange succeeded. Read-only checks of all six
canonical file identities returned HTTP 404 to the new service account. This does
not prove missing files; existing canonical IDs remain unchanged. File access has
not yet been granted/verified for this identity. No workbook, source database,
Forecast or Outcome changed during this check; HOME not installed/restarted.

Next: grant editor access to each of the six canonical books only; verify identity,
parents and tab schemas through the actual service account; grant folder viewer
access only if required to expose parent metadata. Then run the existing dry-run
and separately controlled HOME installation/activation procedure. No production
readiness or forecast quality claim follows from token exchange.


## Current-main compatibility and collector restart — 2026-10-16

The existing PR branch now incorporates main fcb8ece387085b619d09b043bb1d6a4bc7068662,
including session reading/rules and separate BR research quarantine. Earlier
"not yet created" Cloud setup notes are historical: the explicit service-account
provider exists, but read-only checks of all six pinned files still return 404.
This indicates unavailable access for that identity, not deletion of the books.
The owner must share the existing books with the service identity; never create
replacement books or publish credentials to resolve this refusal.

One confirmed collector defect was found: PID-only startup exclusion accepts any
process with the old PID. Windows can reuse that PID for an unrelated process,
causing a false ALREADY_RUNNING and no refresh. The candidate refresh script now
holds an OS named mutex for the whole loop; the PID file is diagnostic only.
Concurrent launch refuses, normal exit releases, and crashed-owner abandonment
permits restart. Tests execute only the extracted lease function in disposable
PowerShell processes; they never execute the collector or contact MOEX/HOME.
No unrelated process needs to be stopped or modified.

Regression checks explicitly exclude BR quarantine from the operational export,
preserve pre-existing 26-column research rows and retain installation receipts
in SYNC_STATUS. Preserving archive rows does not independently certify them.

Calendar evidence remains separate: the current calendar provider reads trading
activity dates for an exact security, while stored session/range markers alone
do not prove intraday boundaries, completeness or first historical availability.
The mirror retains UNVERIFIED and does not infer missing bars, join incomplete
groups across sessions, or adjust prices. Source responses and official session
rules covering each exact contract/date must be independently checked before
calendar attestation. Present-day collection cannot establish historical first_seen.
No new source-history download, clock change or calendar admission is part of
this compatibility correction. HOME activation remains a separate control step.


### Actual Google access and representation defects — 2026-10-16

The owner granted edit access to the six existing books and viewer access to
their archive parent. Exact book identity/name/folder and D1 tab checks now pass
through the actual service identity. No secret or Google row was published.

The isolated, read-only backup-to-live-Sheets preflight exposed numeric date
cells returned by FORMULA/SERIAL_NUMBER (e.g. 44200 = 2021-01-04).
The projection previously parsed every start as ISO text and failed. It now
decodes legacy whole-second serial timestamps only for comparison, validates
the decoded start against the original text record key, and retains every raw
cell value for hashes/readback. It does not rewrite dates/prices, round meaningful
fractional seconds or convert an inferred date into availability proof.

The source snapshot also contains SBER space/T timestamp aliases. Projection now
collapses an alias only when all fields, including provenance/quality, agree
after begin/end representation normalization; any substantive difference blocks.
Source database records remain unchanged. Omitted aliases are counted explicitly.

Isolated preflight is not installed acceptance. Existing price/interval/contract
conflicts must be separately evidenced and resolved, never overwritten to obtain
a successful status. No canonical write or installation was performed here.


### Exact GOLD scope and conservative archive compatibility

The verified snapshot contains both GD futures and an unrelated stock-market
security whose exact SECID is GOLD. The old export OR clause mixed them. Futures
exports now require futures/forts and the exact family SECID pattern; stored
sessions use the same contract pattern. No source record is removed or rewritten.

Read-only comparison found stricter false completion flags in archived IMOEX H1
and missing RTSI index volumes where the source has zero. Compatibility is
directional: existing false completion remains false when the source interval
is completed; existing missing index volume remains missing when source volume
is zero. Both differences have explicit result counters. NULL is never declared
equal to zero, and no full source-payload match is claimed. The reverse completion
disagreement, other volume disagreements, OHLC and interval conflicts still fail
before writes. These rules preserve conservative archive quality, do not certify
minute coverage, and do not admit any row to Forecast.


Actual BR service-sheet samples also contain numeric dates. Service identity
comparison now normalizes their representation only, preserving raw existing
rows and scope labels, so an ISO source session does not duplicate an existing
serial-date session. Distinct evidence scopes remain distinct; a non-midnight
value cannot be silently converted to a trading date.


The actual IMOEX SYNC_STATUS has a fully blank separator at row 20. It is
preserved at its original position; blank rows are not status keys. Nonempty
rows with empty keys and repeated nonempty keys still fail before writes.
Status updates skip separators and retain all older receipts.


The launcher now verifies the installed revision independently of the environment
assignment rewritten by the existing deploy/rollback controller. Both dynamic
startup and a matching controller-pinned SHA pass; a different SHA fails before
credentials/server startup. This prevents the controller rewrite from removing
the git invocation whose exit status the old candidate launcher depended on.
The production Python currently lacks google-auth; the pinned dependency must
be installed as part of the separately authorised activation, never assumed.
