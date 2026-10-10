# G2 — Single-Store Atomic Prospective Staging (2026-10-10)

## Why a new bounded technical prototype

The existing PR #162 staging bridge deliberately uses two independent databases (SQLite receipt and DuckDB canonical Forecast/Outcome). It is crash-recoverable but **not atomic**. Simply adding a second SHA audit does not make cross-file commits all-or-nothing.

This separate Draft PR is a **disposable, explicit staging-only alternative**. A single physical DuckDB file holds the existing canonical `forecast_records` and `outcome_records` *with unchanged schemas*, plus `g2_atomic_captures` containing immutable original source bytes/receipt SHA and `g2_atomic_futures` containing original future evidence/SHA. Each capture transaction writes source and Forecast together; each observe transaction writes future evidence and Outcome together. Failpoints after the first or second insertion deliberately raise an exception **before COMMIT**; true DuckDB rollback leaves both tables empty, including after close/reopen.

## Scope and expected verification

- New module: `src/birzha/storage/prospective_single_store.py`, no change to existing Forecast/Outcome providers, `prospective_capture`, branch-owned #163 reaction/holding, release manifest or engine.
- New tests verify both capture failpoints, both outcome failpoints, restart/idempotency, canonical read through the existing DuckDB Forecast/Outcome journal reader, original raw SHA binding and collisions, tamper detection and invalid clock/calendar/SECID.
- CI needs installed true DuckDB. Full repository pytest and exact-head synthetic-only ephemeral no-push merge with PR #163/PR #147 are separate checks. Never mark them PASS before actual completion.
- No live MOEX request, no actual prospective outcome, and no existing production DB path.

## Important limitations

This is **not** a production migration or a replacement already wired into the HOME runtime. It does not retroactively merge prior SQLite/Forecast/Outcome evidence into a canonical file; old receipts and frozen predictions remain untouched. It is single-process with one locked DuckDB connection, not a measured multiprocess/multi-host outbox or durable remote replication. Local SHA is not third-party source authentication or independently certified UTC time; `calendar_origin` strings are supplied by caller and must still be checked against trusted exchange evidence.

The physical DuckDB staging file is one transactional authority for this bounded test, **not a second permanent production source of truth**. Activation requires independent review, verified actual source/calendar, concurrency/backup/restore and explicit main/HOME production authorization. Until then all release gates stay BLOCKED, and future 5/10/20 real outcomes remain PENDING.
