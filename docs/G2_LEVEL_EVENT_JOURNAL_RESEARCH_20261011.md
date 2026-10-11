# Q-085: First-receipt Level Event Journal (isolated research pilot)

The new application module g2_level_event_journal.py is an opt-in,
single-completed-M15 event-time journal in an isolated .sqlite3 file.
It is NOT wired to the live MOEX provider, HOME, existing Forecast journals,
trading, production, or protected historical OOS. No automatic scheduler
is added. The caller must explicitly supply genuine new M15 captures,
an exact Instrument/Candle contract, original bytes and a positive research
ATR. All external provider/clock assertions remain unauthenticated.

The pilot commits one receipt in a SQLite transaction with immutable
first_received_at, source-provided observed_at, raw bytes, SHA-256 and
previous-hash content chain. Identical retry does not alter first receipt;
different evidence for the same instrument/end time raises Collision.
Stale, partial, non-EXACT, missing, noncausal, overlapping, late historical
or mixed-source bars are rejected. Source-payload-to-candle correspondence
is NOT independently proven by this research-only pilot.

On its 20th contiguous first-received M15 bar, the journal emits
LEVEL_FIRST_DETECTED for each support/resistance rolling extreme. Only
later bars may produce LEVEL_RETEST_HELD or LEVEL_PRICE_UPDATED. A changed
rolling extrema price is NOT evidence for a role flip. Receipt and event
records have append-only database triggers and internally checked SHA chains.
Restart/retry/gap/tamper/late-source negative tests accompany the code.

This is distinct from Q-084 reconstructed history: its event time reflects
the actual receipt performed by this pilot, conditional on the caller having
supplied truly new market data. It is not proof of earliest market availability,
independently TSA-attested time, exchange source truth, or genuine predictive
power. ATR comes from caller and is only a research parameter. No prediction
or trading signal is issued by the new journal. Gaps need 20 fresh consecutive
bars; a real calendar-aware session adapter is a separate guarded task.

Q-085 engineering acceptance requires successful exact-head ephemeral
CI on #168/#179/#147 source dependencies, security/preflight and unchanged
release gate of 12 blockers. No authority for main merge, HOME deployment,
production execution, current Forecast V1/Outcome edits, or protected OOS.

A later identical retry of an already accepted bar is allowed even when the current
clock has advanced; it is read-only and always preserves the FIRST receipt time.
A new stale bar is still refused. This distinction is regression-tested across
SQLite process reopen and conflicting-source retry.

An M15 observation gap now explicitly emits LEVEL_HISTORY_GAP for any active research
support/resistance candidates. Once 20 new consecutive bars arrive, the first
level in that new segment is LEVEL_FIRST_DETECTED, never a retroactive retest of
old levels, even if the numeric extrema coincidentally match before and after.


## Q-086 internal integrity audit

Research-level original receipt SHA was previously verified, but redundant SQLite indexed columns (stream/bar_start/bar_end/observed) and event linkage (receipt_id/stream/role/kind/price) were not independently compared to their hashed payload. Q-086 verifies these invariants on journal open and explicit verify(), rejects malformed/mismatched rows and impossible simple event-state transitions; existing missing append-only triggers and dropped tables are refused rather than silently re-created. Negative tests intentionally mutate only unprotected index fields and restore the trigger: the hash may remain valid, yet verify must fail. Entire DB rewrite with consistent rehashing, external source authentication, independent TSA and source-payload-to-Candle binding remain UNPROVEN. The journal remains opt-in offline staging research; never production or HOME.
