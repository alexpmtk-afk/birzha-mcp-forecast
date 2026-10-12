# Q-093 — SQLite first-local M1 intake crash safety

This is an isolated research-only amendment based on Q-092, not an
approval to run a live exchange collector.

### Changes

- Force SQLite synchronous=FULL and refuse if this guarantee is unavailable.
  On each local intake journal open, run PRAGMA quick_check and refuse a
  corrupted file rather than accepting its contents.
- Wrap every first-time table, trigger and schema-version initialization
  inside one BEGIN IMMEDIATE / COMMIT SQLite transaction. A kill while
  creating schema objects must NOT leave an orphaned table that refuses
  normal later initialization.
- Preserve the existing BEGIN IMMEDIATE transaction surrounding original
  raw bytes, source hash, assigned local timestamp and chained receipt.
- Explicitly test hard subprocess exit while schema is half-initialized and
  after the receipt INSERT but BEFORE COMMIT, followed by successful
  restart, integrity audit, append and duplicate idempotency.
- Retain the existing observed HTTP-response timestamp as a *caller claim*
  and local first-receipt timestamp as *local clock only*; neither is a TSA.

### Acceptance and remaining boundaries

Test on temporary SQLite databases only; no live services or production
database. Existing FIRST local receipt id/time survives a restart, an
unfinished write is rolled back, and an unfinished schema is not visible.
This is application-level crash and transaction behavior, NOT proof
against arbitrary sudden storage media loss, malicious DBA rehash,
authentic MOEX transport, approved session calendar or independent TSA.

Exact-head full crossline CI, Security and Preflight must pass. All twelve
release blockers remain unresolved. NO_MAIN, NO_HOME, NO_PRODUCTION and
NO_FORECAST/NO_TRADE.
