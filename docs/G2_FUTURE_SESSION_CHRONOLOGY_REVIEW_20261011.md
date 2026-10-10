# G2 independent review — future session completion time contract (2026-10-11)

## Scope and finding

Independent semantic audit of ChatGPT-owned disposable DuckDB single-store staging #168, with exact-head compatibility against Codex-colleague #169 (includes #167) and research #147. No colleague modules were edited.

**Confirmed code defect (pre-fix):** `_validate_future()` used `tuple(sorted(ends)) == ends`, which accepts *equal* completed_at timestamps for different exchange-session dates. The lower-bound-only assertion `completed_at MOSCOW_DATE >= declared_session_date` also permits completion many days after the nominal session, while treating it as a valid completed D1 candle. A claimant could therefore pass malformed future inputs to the append-only Outcome path. This is input-contract correctness, not proof that the original real-world provider ever emitted bad data.

## Narrow repair

- Require canonical ISO session dates and strictly increasing D1 completion instants (not merely nondecreasing).
- A completed D1 session must finish on its declared Moscow session calendar day or, for overnight sessions, the **immediately following** Moscow calendar day; never multiple days later. Check actual timestamps `<= source_observed_at`, as before.
- Perform the same new chronology checks on stored V2 nested future evidence at audit/readback, so old staging rows with inconsistently authenticated but internally matching local SHA are not silently treated as valid.
- Re-check exact ordered equality of retained `session_dates` vs `expected_calendar_dates` on audit.
- Preserve the immutable legacy forecast and canonical Outcome shapes, the staging V2 schema and fail-closed release-gate. Existing staging V2 records are read only; any malformed row must fail audit rather than be repaired in place.

## Regression evidence required

Negative cases:
1. Different future session dates carrying identical completed timestamps.
2. Strictly ascending completed timestamps all several calendar days after their declared sessions.
3. Legacy/persisted V2 nested evidence edited with internally matching SHA but completion timestamps attributed to wrong sessions: `audit()` must reject.

Positive: existing real DuckDB transactional/crash, restart, duplicate proof, and complete multi-horizon tests still pass.

## Known limitations (do not increase admission claims)

A one-calendar-day overnight allowance is a **local format sanity bound**, not a certified MOEX trading schedule. Caller-supplied `VERIFIED_EXCHANGE_SESSION_CALENDAR` remains unauthenticated by this module; secure first-receipt timestamp, true next 5/10/20 exchange sessions, physical power-loss recovery and production HOME acceptance require independent evidence. The expected 2023–24 holdout and actual later market outcomes remain untested. This fix does not authorize merging, deploying or trading.

## Follow-up

Independent domain review must check overnight/session-date conventions against actual MOEX product calendars, and strict next-session schedule provenance must be externally verified before any live outcome release. PR #170 keeps the release machine pins #168/#169/#147, and this change is a stacked non-production correction. No main merge or HOME access.
