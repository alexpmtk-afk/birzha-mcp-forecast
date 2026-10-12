# Q-114–Q-119: staging recovery, Outcome integrity and release evidence

Base: Draft PR #223 exact HEAD 3080dfdd1bd3b1820fcd08a28c6fcea591deeeb2.
All actions limited to GitHub source and temporary CI research databases.
No HOME, production, live forecast, protected OOS or trading operation.

## Q-114 — full DuckDB staging schema transaction

The single DuckDB staging journal created g2_atomic_meta, both
canonical tables, source/future receipts, TSA anchor and calendar
evidence tables through independent autocommits. A process exiting
mid-init could leave an incomplete database. Initialization now uses
one BEGIN TRANSACTION/COMMIT across the complete versioned schema;
rollback and close on any ordinary initialization failure. A unique
DURING_INITIAL_SCHEMA fault hook permits hard process exit
in disposable subprocess tests; no user machine process is affected.
The child exit happens before schema COMMIT and cold reopen must see
empty complete schema, not a partial durable database.

## Q-115 — do not silently repair an existing incomplete schema

Prior reopen used CREATE TABLE IF NOT EXISTS, silently supplying
missing tables in a previously interrupted or corrupted journal.
Now any preexisting schema must contain exactly the seven expected
staging tables before initialization. A partial, inconsistent or
unexpected table inventory is ImmutableCollision; NO changes occur.
Tests inspect the unchanged original file after rejection.

This may also refuse experimental pre-Q114 isolated DB files, which
require deliberate migration/owner review rather than implicit repair.

## Q-116 — canonical Outcome column integrity after restart

An Outcome's serialized body and hash could be internally consistent
while denormalized outcome_records.forecast_id/horizon_sessions columns
were changed independently. Cold audit now binds these columns to
g2_atomic_futures original receipt/forecast/horizon and accepted
5/10/20 horizon before returning a valid journal state. Negative
tests modify only columns and confirm cold ImmutableCollision.

## Q-117 — SQLite Outcome association and mathematical re-derivation

Existing pilot cold audit committed the original source and future
window SHA but did not rebind Outcome's forecast_id, horizon, exact
SECID/market, capture timestamp, reference_price or predicted
direction to the frozen ForecastRecord. Cold audit now reconstructs
those facts from the original stored Forecast/receipt, and recalculates
the observed return and UP/DOWN direction_hit from immutable final
close and reference price. Coordinated rehash of a false Outcome
cannot replace actual stored model/future facts.

## Q-118 — causal source-observed and written-at ordering

Pilot audit now checks last completed source candle <= caller
observed-at <= outcome_written_at, and observed-at > original
prospective capture_at. Receipt and predicted T0 are validated
earlier. Rehashed fake source receipt dates and backwards Outcome
write times are rejected. This remains an internal clock/source
contract, NOT independent network receipt/TSA certification.

## Q-119 — exact release NO-GO evidence inventory

The canonical docs/G2_RELEASE_GATE_20261010.json is historical,
dated 2026-10-10; it must not be silently rewritten to mark
technical staging hardening as external authenticity or skill.
The release test still requires 12 blockers (10 technical/evidence
plus 2 explicit user approvals). The unresolved external work is:

- Independently authenticated MOEX transport, original first
  receipt and trusted timestamp anchor at capture (cannot
  retroactively create missing historic first receipts).
- Official complete exact-SECID exchange calendar and trusted
  observed source through all genuine prospective 5/10/20 outcomes.
- Independently reviewed Protocol08 Control/Route/Alignment rules,
  matured prospective forecast skill and untouched holdout evaluation.
- Authorized production migration with tested backup/rollback,
  read-only HOME Windows acceptance and explicit merge/deploy approvals.

Research tests, code review and local signed hashes are NOT the
external evidence needed for those blockers. No release state is
changed. Any user permissions require direct explicit authorization.

## Required acceptance

Current exact HEAD must pass pinned ephemeral crossline no-push,
focused tests, full regression, compilation, Public Security and
Repository Preflight. All new negative cases must fail correctly;
both post-crash reopen and unchanged canonical receipts must pass.

No main merge, HOME request, REMOTE access, production DB or
Forecast V1 score modification.
