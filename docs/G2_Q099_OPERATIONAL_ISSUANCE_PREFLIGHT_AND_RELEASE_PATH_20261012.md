# Q-099 — operational six-market analysis: avoid unsafe pre-issue history writes

Research-only correction based on Draft #217 Q-098. This does not
change Forecast V1 prediction calculations, the authenticated source
scope, release gates, current main or the HOME installation.

## Confirmed operational defect

Before this change, the real MCP function analysis.run_core delegated
to MarketAnalysisService.run. It invoked historical_data.sync for up
to SIX instruments, involving network and durable historical cache
writes, BEFORE calling ForecastJournalService.create_and_save. The
latter has an unconditional AdmissionRefused for
UNATTESTED_FORECAST_ISSUANCE because old ForecastService.build uses
market EVENT time rather than original source-receipt + issued-at T0.

Consequently, if any D1 syncs succeeded, they could modify historical
cache/state even though all subsequent prospective journal issuance
attempts were known to be forbidden. This is unnecessary and misleading
for a purported all-in-one forecast request. It is an existing runtime
ordering error independent of the Q-085..Q-098 staging research path.

## Minimal fail-closed fix

- Add a pure, typed operational issuance_preflight status to
  ForecastJournalService. Current status is BLOCKED; no source checks,
  network calls, calendar/TSA claims or canonical writes occur here.
- Check that preflight in the actual MarketAnalysisService before any
  historical D1 source sync, and return a six-symbol BLOCKED report
  (blocked count separate from passed/failed) with zero history-sync
  attempts, zero forecast issuance attempts and production permission
  false.
- Reject forged positive preflight from subclass: a separate approved
  implementation is required, not a boolean flip or ad-hoc capability.
- Update the MCP analysis.run_core description so the user is not told
  the blocked tool will refresh/cache data and write six forecasts.
- Existing fake Journal fixture preserves prior behavioural coverage
  for the nonoperational service unit tests; no unauthenticated source
  is fetched in the new acceptance tests.

## Required negative tests

Use inert spies that deliberately raise if source synchronization,
ForecastService.build or journal.append runs in the blocked operational
path; assert all counters remain zero and the six symbols have BLOCKED
status. Attempt a forged positive preflight and ensure it is refused
before any network operation. Assert the MCP declaration describes its
guarded semantics.

## Release/evidence truth

This change avoids an avoidable cache/network side effect and
misleading run result; it DOES NOT CLOSE the canonical
live_forecast_issuance_after_source_receipt gate. Operational issuance
is still forbidden, not suddenly implemented. Original market source
transport, independently trusted clock, official calendar and genuine
prospective outcomes are not supplied. Protected 2023–2024 OOS,
production migration, HOME protected commands, real-money trading and
merge/deploy require separate approvals.

Q-099 exact-final-HEAD full focused/crossline CI, Security and
Preflight need PASS. All 12 production release blockers remain open.
