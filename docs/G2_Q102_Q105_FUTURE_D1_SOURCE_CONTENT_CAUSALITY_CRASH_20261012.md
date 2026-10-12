# Q-102–Q-105: four-part future D1 integrity and staging safety

Based on Q-101 Draft #220 exact HEAD
3b234793b1df37e81bc8aeb496810f5886e3810e.
Scope: isolated source/Outcome staging research only; no real MOEX,
production, HOME, Forecast V1 or official exchange-calendar admission.

## Q-102 — compare real retained bytes to declared future window

Existing Pilot/SingleDuckDB outcomes accepted `source_payload` as opaque
bytes. A source SHA established blob identity but NOT whether its
individual D1 close prices, calendar dates and closing times agreed
with the caller-supplied future 5/10/20 arrays. Added a strict native
ISS `candles.columns/data` replay path requiring a complete 1-to-1
match of each begin/day, end time and close. Inconsistent native source
rows, missing/extra rows, duplicate keys, wrong prices and malformed
attempted ISS payloads refuse before a new outcome is appended.

The older synthetic test fixture without native ISS candles remains
accepted only as development-only UNREPLAYED input and can NEVER
establish actual official source provenance. This is intentionally not
a silent migration of long-existing synthetic examples.

## Q-103 — caller-provided VERIFIED strings are not evidence

Both stores now persist explicit booleans:
`native_d1_original_rows_match_claims`,
`source_transport_independently_authenticated=false` and
`calendar_independently_authenticated=false`.
Persisted records are checked on each cold audit by replaying the
actual stored native source bytes. A correctly shaped self-submitted
page may prove content consistency, never that the remote exchange,
first receipt time or official session calendar was authenticated.
If a caller modifies boolean claims and rehashes the stored receipt,
the journal refuses rather than upgrading to independently certified.

## Q-104 — exact future D1 chronology in both storage implementations

The SQLite pilot previously checked only that each completed timestamp
was no earlier than its declared Moscow day and used a non-strict
ordering test. This allowed the same completed timestamp across
distinct declared sessions or arbitrary long delays. The pilot now
requires canonical session ISO dates, completion on that Moscow date
or the immediate next day, and strictly increasing completion times,
matching the stricter DuckDB staging logic already introduced earlier.

## Q-105 — atomic startup and fail-closed crash recovery

The prospective pilot previously autocommitted table/trigger DDL and
version creation in separate operations, risking partially initialized
schema after a hard process exit. Startup now sets PRAGMA
synchronous=FULL and checks quick_check, and initialization includes
tables, append-only triggers and version in ONE BEGIN IMMEDIATE/COMMIT
unit. An adversarial disposable child-process test exits mid-schema
and validates clean reinitialization and successful subsequent
capturing after restart. No actual HOME/REMOTE process was killed.

## Acceptance and unresolved truth

Exact final SHA full/crossline CI + compiler + Public Repository Security
+ Repository Preflight and unchanged release gate are mandatory.
Neither valid synthetic D1 rows nor any of these software invariants
closes any of 12 official release blockers: live independent MOEX TLS
origin, first network receipt/clock/TSA, independently authenticated
exact-SECID official calendar, genuinely completed prospective outcomes,
Protocol08 semantic components and explicit merge/HOME approvals
remain unavailable. No merge, deployment, protected 2023–24 OOS,
production DB or trading.
