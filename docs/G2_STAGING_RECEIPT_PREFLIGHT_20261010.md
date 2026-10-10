# G2 Staging Pilot Receipt Preflight — 10.10.2026

**Owner:** ChatGPT independent forecast/prospective line. **Base:** Draft PR #156 at `5f091403510a7af0a4c036404db7c04af959120d`. This fix does **not** change the teammate's regime/price/level modules.

## Independently reproduced failure

`CanonicalProspectiveStagingBridge.reconcile()` runs `ProspectivePilotLedger.audit()` before recovery, but the *ordinary* `capture()` and `observe()` paths previously read existing stored `receipt_json` and `payload_json` without the SHA audit. A privileged edit that changes an existing SQLite payload but leaves its SHA unchanged can bypass those normal bridge reads. SQL no-update triggers protect ordinary writes but are not a hash validation of a re-opened datastore.

Three negative tests first **failed on the unmodified baseline code** (DID NOT RAISE): altered duplicate forecast receipt on `capture`, altered forecast receipt on `observe`, and altered existing future outcome payload on repeat `observe`. They now **PASS with fail-closed `ImmutableCollision`**.

## Repair

The bridge runs `pilot.audit()`:
- before processing a normal `capture` request;
- after persisting the pilot capture receipt and **before** the canonical Forecast Journal append;
- before reading the pilot receipt for `observe`;
- after pilot outcome insert/identical replay and **before** the canonical Outcome Journal append.

The recovery path already audited first and is unchanged. No schema, provider, trade logic, issuance clock, ForecastRecord/Outcome semantics, or production routes are changed. No previous frozen forecast is rewritten.

## Remaining limitations

This is full-pass hash validation for the **disposable staging** pilot. It may be O(number of rows × stored raw-byte size) per bridge write. It neither solves cross-DB atomicity nor makes Python/SQLite hash records tamper-proof against a privileged attacker who can change both the payload and its digest. Concurrent privileged writes between audit and operation are not prevented by a global cross-store lock. There is no externally signed provider receipt, no trusted UTC third-party timestamp, no complete exact-SECID calendar, and no production-level durable outbox.

The empty/full-future market outcome gates remain unchanged; **release remains BLOCKED**. This module adds detection of simple stored-content corruption at the point of use, not independent proof of source authenticity or prediction skill.

## Verification

Local isolated synthetic fixture: **3/3 negative tests fail on prepatch**; **16/16 non-DuckDB bridge tests pass after patch** (one true DuckDB integration test separately exercises GitHub CI). GitHub Actions feature-branch workflow runs all repository pytest with real DuckDB and the targeted prospective/source-issuance tests. Final pass is recorded only after observing the actual Actions job results.
