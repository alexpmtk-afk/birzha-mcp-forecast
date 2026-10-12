# Q-101 — exact MOEX-local future-session causality and pilot-window integrity

Base: Draft PR #219 HEAD 0ed91b4137c6edba0d4e8ce7b675e74948a42794.
Scope: DEVELOPMENT-only prospective pilot (SQLite) and existing single-DB
DuckDB staging. No live MOEX/TSA/HOME, Forecast V1, production, or main.

## Confirmed UTC-versus-Moscow causality error

Both SingleDuckDBProspectiveStagingJournal._validate_future and
ProspectivePilotLedger.observe formerly compared each claimed MOEX
session date against the UTC date of forecast.created_at_t0. A forecast
issued 2026-10-10 23:30 UTC was already 2026-10-11 02:30 MSK.
The earlier code could incorrectly call 2026-10-11 a FUTURE session.
That is a causal lookahead defect in the research staging Outcome chain.

Fixed both writers to require every claimed future MOEX session date to
be STRICTLY after the forecast T0 **Moscow-local calendar date**. The
DuckDB cold-audit also applies this rule to previously stored future
windows, rejecting otherwise hash-consistent legacy data.

## SQLite pilot cold-audit gap

ProspectivePilotLedger persisted the target session/terminal close, but
not the complete list of 5/10/20 claimed future session dates, completed
timestamps and intermediate closes; duplicate checking ignored altered
interior bars when terminal close and original raw SHA were unchanged.

Now the isolated pilot stores immutable `original_future_window` for all
session dates, expected calendar dates, per-session completed-at UTC and
closes, with canonical SHA. Duplicate results must match its SHA and
existing terminal outcome fields. Reopen audit checks the full ordered
window, equality with claimed calendar, terminal outcome consistency,
offset-aware completion chronology and every date after forecast's
Moscow-local T0 date. Legacy pilot Outcomes lacking this window fail
closed; no automatic migration or retroactive reconstruction is
performed. Historical files require independent explicit review.

## Focused acceptance cases

- Forecast T0 2026-10-10 23:30Z / 2026-10-11 02:30 MSK: claimed
  2026-10-11 cannot count as future; error before Outlook append.
- Correct five distinct dates 2026-10-12..16 are accepted by the
  isolated fixture under both SQLite and DuckDB; duplicate after cold
  restart remains identical.
- Simulate previously written DuckDB data with the former UTC calendar
  comparison; cold read must refuse and preserve existing bytes.
- SQLite pilot must reject a changed intermediate close even when
  terminal outcome matches. A rehashed manipulated first date after
  restart must fail the cold-audit despite internally consistent SHA.
- Original D1 provider raw source, asserted session calendar and time
  remain caller inputs. This is NOT an independently authenticated
  MOEX session/calendar or prospectively matured evidence.

Release gate remains unchanged: G2_RELEASE_ALLOWED=false; 12 blockers
(10 technical and 2 explicit user permissions). Protected 2023–24 OOS,
production journal, trading, external TSA, main merge and HOME deployment
remain prohibited. Complete full SHA-pinned crossline CI, security and
preflight before technical acceptance.
