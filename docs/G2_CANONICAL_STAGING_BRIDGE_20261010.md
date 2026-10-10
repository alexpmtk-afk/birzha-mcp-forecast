# G2 Canonical Forecast/Outcome Staging Bridge — 2026-10-10

## Scope and ownership

Independent ChatGPT engineering line. Stacked PR order: #137 research dataset -> #139 Development baseline -> #141 prospective isolated pilot -> this PR bridging existing canonical DuckDB journals. Codex-colleague's TREND/BALANCE files are untouched. No HOME, deployed Forecast Engine, MCP, live trade, 2023–2024 holdout, merge or deploy.

**Motivation:** the existing canonical DuckDBForecastJournal and DuckDBOutcomeJournal are already the intended forecast/outcome stores. Deploying a parallel SQLite-only production journal would create conflicting sources of truth. Here the existing canonical journals receive actual ForecastRecord and HorizonOutcome objects; the staging-only SQLite receipt maintains original source bytes, receipt timestamps and SHA.

IMPORTANT: there is no atomic commit across two separate databases. This design is **isolated staging only**, with receipt-first writes and explicit replay/recovery after a partial failure. It does not claim production-grade multiwriter durability or historical proof.

## Artifacts

- src/birzha/application/prospective_staging_bridge.py: CanonicalProspectiveStagingBridge.
- src/birzha/application/prospective_capture.py: add read-only get_capture_record() and list_outcomes() for journal recovery.
- tests/test_prospective_staging_bridge.py: negative/replay/collision tests and true DuckDB integration test when dependency is available.
- .github/workflows/g2-prospective-staging-integration.yml: feature-scoped CI with pinned DuckDB 1.5.5 and pytest 8.4.1, plus synthetic pilot demo.

All three stores must be file-backed under an explicit disposable staging directory. Production-like paths, in-memory journal defaults and callers requesting PRODUCTION mode are rejected. No background process or user actions can be triggered by this module.

## Crash consistency protocol

1. CAPTURE: validate and atomically persist original forecast JSON, source bytes and receipt in the isolated pilot; then call canonical Forecast Journal append with the original ForecastRecord. Read back and compare canonical SHA to the source-bound receipt. A failed canonical append leaves a pending sidecar record. Replaying the exact same original record heals the missing canonical row, without changing capture time; reconciliation reports PENDING_FORECAST_REPLAY until resolved. The bridge never invents a ForecastRecord from stored JSON.
2. OUTCOME: only when both sidecar receipt and canonical forecast already agree, validate exact SECID/market, horizon, session calendar, completed futures and source receipt time; persist immutable original future evidence; append to canonical Outcome Journal with the existing deterministic outcome-id formula. If second append fails, reconcile finds the missing canonical outcome and replays it from the sidecar **without redownloading market prices**.
3. RECONCILE: hash-audit sidecar, verify canonical forecast identity and detect orphan/mismatched canonical outcome records. Fail closed instead of overwriting an old record. Only absent canonical outcomes can be auto-restored. Captures lacking a canonical Forecast Record require an exact original ForecastRecord replay.

Canonical HorizonOutcome max_favorable_excursion_pct and max_adverse_excursion_pct remain NULL: the staging pilot has validated closes only, **not a verified intraperiod high/low path**. A first-touch/stop-target claim is not justified by this data.

## Executed local verification

Python 3.13.5, isolated offline tests including previous 10 baseline + 17 pilot + 14 bridge cases: **40 passed, 1 skipped**. The skipped case is the actual DuckDB journal integration, which cannot run in the local environment due to the unavailable dependency. It is explicitly included in GitHub feature-scoped CI, which must be read back separately before claiming true DuckDB PASS.

Tests cover restart/idempotency, original receipt preservation, conflicting forecast/source, invalid provenance and clock, exact session/SECID, missing/extra future bars, partial forecast and outcome failures, canonical recovery, incompatible record identity, orphan outcomes, SQL UPDATE/DELETE failure, tamper detection and distinct staging paths. No actual MOEX prices or prospective first receipt were acquired; fixture data are synthetic and never used for model-quality claims.

## Remaining activation gates

1. Real source acquisition adapter via existing MOEX request governor, returning **original raw provider response bytes**, authenticated source identity, independent measured ingestion/observed_at and completed D1 candle evidence. Caller-supplied origin strings or calendars are NOT adequate proof.
2. Immutable mapping between captured payload SHA, source event/available/observed/decision timestamps, snapshot_id, exact contract SECID and forecast_id, with an external time/durability anchor.
3. Replace staging two-DB pattern with one audited canonical production journal + atomic outbox, or prove robust crash/multiwriter/backup/restore semantics before production; no permanent competing ledger.
4. Full verified OHLC session highs/lows for excursion/first-touch metrics, otherwise keep those fields unknown.
5. Independent stacked code review, complete pytest including actual DuckDB integration, restore/restart and clock-skew tests, bounded real read-only provider sample. Distinct user authorization for merge/deploy/HOME/schedule/production DB.

This PR is a tested staging implementation, not a deployed real market capture or a validated forecasting model. Codex-colleague continues independently on TREND/BALANCE.
