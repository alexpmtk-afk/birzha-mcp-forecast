# G2 single-DuckDB: strict source and full future-window replay evidence (2026-10-10)

## Independent contract audit and specific bug

The predecessor staging journal in Draft PR #166 correctly atomically stores original source bytes and canonical Forecast/Outcome, but duplicate detection was **incomplete**:
1. When repeating `capture` with unchanged raw source and forecast payload, mutated `CaptureEvidence.source_observed_at` or `latest_completed_event_end` still received `DUPLICATE_IDENTICAL`. Those provenance fields were already in the original receipt but not compared to the caller's repeated evidence.
2. When repeating `observe` with unchanged source bytes, terminal close, terminal end and calendar, altered **intermediate candle_close** or **intermediate candle_completed_at** were accepted as `DUPLICATE_IDENTICAL`. Canonical HorizonOutcome intentionally projects the *terminal* close; it is insufficient as a fingerprint for the complete supplied future input window.

These cases do not prove provider source fraud or predictive skill. They are local immutable evidence-contract failures: the same result can hide distinct caller input. Four targeted negative regressions now demand `ImmutableCollision`.

## Narrow correction

- On duplicate capture, compare normalized original source observed and completed times, fixed snapshot and SECID, source origin, forecast record version and frozen T0 against the durable original receipt before declaring `DUPLICATE_IDENTICAL`.
- On outcome creation store `future_input_evidence` with exact-SECID market, source/calendar origin, original raw source SHA, observed time and **all** supplied session dates, expected calendar dates, candle completion stamps and finite closes, plus a SHA-256 of canonical input evidence. On replay require the full fingerprint unchanged, not only the final target candle.
- During on-file `audit()` verify the nested input SHA and agreement of the full-window input's terminal candle, calendar, raw SHA and market/SECID with the existing canonical HorizonOutcome.
- Advance **disposable staging schema** from `G2_ATOMIC_SINGLE_DUCKDB_STAGING_V1` to **V2**. Existing V1 staging files are explicitly rejected rather than silently reinterpreted; no migration runs or previously frozen forecast is rewritten. No production persisted data was ever changed.
- Preserve one-file transaction, existing DuckDB `forecast_records/outcome_records` schemas and original source bytes; no new production ledger.

## Tests and limits

Regression tests exercise two mutated source-receipt timestamps, altered intermediate future close and completion stamp, identical restart/idempotent replay, nested hash tamper under recomputed outer SHA, old V1 schema rejection and the previous interrupted-process, cold backup and full remaining source/feature tests. No new live MOEX data, calendar or predictive claims. The nested SHA protects against simple inconsistent modification, **not** a privileged attacker who recalculates all related hashes. First-receipt authenticity, trusted receipt clock, actual power loss, multi-host failover, verified exchange calendar and matured 5/10/20 future outcomes remain separate blockers.

The GitHub CI workflow pins exactly new independent **#167** colleague head `c5b815dd59dd226caf288aeb387b172911fefd4c` (includes previous #165/#163) and **#147** regime head `82af27e1f9d79f02f8062c5756047fe1fb35ef20` in temporary no-push merges only. **CI results must be inspected before PASS is claimed.** HOME, production, merge to main, existing database files, protected 2023–24 data and colleague-owned modules are untouched. Release remains BLOCKED.
