# G2 HOME and LIVE T0: independent read-only audit and source-bound write guard

Date: 2026-10-11. Technical assessment, not a production rollout or authorization.

## Root cause 1: operational Forecast Journal could persist an event-time T0

Actual code path audited:
- `src/birzha/mcp/server.py`: `forecast.create` directly calls `_journal.create_and_save(symbol, as_of_date=...)`.
- `src/birzha/application/market_analysis.py`: automatic market-analysis workflow also calls `self.journal.create_and_save(symbol)`.
- `src/birzha/application/journal.py`: prior service built via `ForecastService.build` and appended directly to the configured journal with no source receipt or issuer time.
- `src/birzha/application/snapshot.py`: ordinary non-knowledge-cutoff snapshot `causal_t0` takes max of completed candle end timestamps; this is the **event time**, not the first observed source receipt or publication/issuer time.
- `src/birzha/application/forecast.py`: the record copies `snapshot.as_of` into immutable `created_at_t0`.

Therefore a recently issued LIVE record could be immutably backdated to a completed candle's event end. The six separately captured uncalibrated source-bound records and their `prepare_source_bound_forecast` code correctly introduce a distinct issued-at UTC after receipt, but this is a **staging-only path**. The operating MCP `forecast.create` and `market.analysis` paths are not routed through that capture and must not be called genuine prospective forecasts.

### Narrow fail-closed mitigation

This PR refuses `ForecastJournalService.create_and_save` before **any** `ForecastService.build` fetch or canonical Journal.append. Both identified callers share this choke point. The error is `UNATTESTED_FORECAST_ISSUANCE`. This is an intentional protective behavior change; `forecast.create` and any dependent market-analysis operation may report failure until a fully sourced and authorized writer is built. `forecast.build` remains a read-only, unvalidated **event-time baseline preview**, and existing `forecast.get/list` remain read-only. Historical Forecast/Outcome contents are not mutated.

A **future, separately reviewed** operational replacement must include: original byte receipt + SHA256, replay-verifiable exact-SECID input windows, independently verified availability before T0, fresh trusted UTC issue T0 after all receipts, bounded latency, atomic durable append and clear idempotency. Do not enable the old `create_and_save` based only on a new label or clock without verifying the originating bytes. Current `capture_prepared_in_staging` is for disposable staging only and cannot be silently swapped into production.

Tests verify no data fetch/append occurs on attempts, including explicit `as_of_date`, both callers reference the same guarded method, and legacy journal read APIs work. Run full CI under exact merged source #168 + colleague #173 + regime #147 and verify release remains BLOCKED.

## Root cause 2: HOME acceptance never executed

Connected **private bridge** [PR #104](https://github.com/alexpmtk-afk/gpt-powershell-bridge/pull/104) was independently inspected:
- Run [38070736976](https://github.com/alexpmtk-afk/gpt-powershell-bridge/actions/runs/38070736976) reached the protected Windows runner but `pwsh` was not installed.
- Follow-up [38070781907](https://github.com/alexpmtk-afk/gpt-powershell-bridge/actions/runs/38070781907) used native `powershell`, but Windows execution policy refused the generated `.ps1` at the runner boundary before the first intended HOME check.
- The intended checks were only path existence, branch, SHA, tracked git status, disk free and UTC. **None produced a reliable HOME acceptance observation**.

This is an **infrastructure/access BLOCKED**, not evidence of absent or corrupt HOME. The safe action is to obtain explicit approval for a platform-compliant read-only diagnostic path or use a previously authorized protected read-only bridge which respects the machine's policy. No `Set-ExecutionPolicy`, `-ExecutionPolicy Bypass`, shell swap, custom launcher, remote run, service restart, production DB inspection or HOME write is attempted here. Without that approved route, the HOME release gate stays BLOCKED.

## Release state

These changes eliminate a *new unauthenticated write path* in the review branch, but do not prove that an approved LIVE source-bound writer exists. Thus `live_forecast_issuance_after_source_receipt` remains **BLOCKED**, not PASS. `home_windows_readonly_acceptance` remains **BLOCKED** due to execution-policy boundary. The 10 technical/evidence gates plus two user approvals remain pending.

The last original source receipts for historical preexisting records cannot be retroactively produced. Future +5/+10/+20 outcomes require genuinely matured sessions and should never be backfilled. Full Protocol 08 is a separate product scope.

## After this PR

1. Independent review of functional impact on `market.analysis` and existing operational consumers.
2. Explicit design of an authenticated source-bound production writer with single canonical storage.
3. User-approved safe HOME read-only access route (not a policy bypass).
4. Follow normal release-gate approval only after real evidence, user approval for merge and separate authorization for HOME deploy.
