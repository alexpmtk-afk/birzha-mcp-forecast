# G2 — isolated DuckDB process-termination and offline recovery evidence

**Date:** 2026-10-10. **Scope:** independent ChatGPT-owned disposable staging testing; stacked on Draft PR #164. Never HOME/production, never a live exchange outcome or trading path.

## Why these tests differ from the prior fault hooks

PR #164 passed full project pytest and fault injection by raising an exception inside one Python process. A Python exception tests transactional ROLLBACK but **does not test OS process termination** or database reopening without the writer calling close(). The next tests use an isolated child process running the test module as a script; the child invokes `os._exit(83)` at four instrumented points. The parent pytest process stays alive and tests DuckDB recovery after exit. A marker file under disposable `tmp_path` proves the requested midway point was reached; it is not part of the original market evidence.

- Capture interrupted after source receipt INSERT and after Forecast INSERT; on restart neither half may be visible, and original capture can retry.
- Outcome interrupted after future evidence INSERT and after canonical Outcome INSERT; old Forecast remains and no orphan Outcome is visible on restart.
- Immediate child-process exit after **successful commit**, without explicit DuckDB close, must preserve committed capture and Outcome.
- An independent second process is refused while the first writer has the DuckDB file open; after first writer closes, the second can open normally. This tests local exclusive writer lock, **not** supported multi-writer transactions.
- Cold backup after **all connections are closed**, SHA-256 byte-for-byte backup/restore into a *distinct* disposable root; readback of original receipt and Outcome, idempotent replay and independent advancement of the restored copy. A restored database with corrupt canonical hash must fail closed. **Do not copy a live DuckDB file in production**; this is deliberately an offline-only test.

## CI and acceptance boundaries

Actual GitHub Linux CI executes the subprocess tests with real DuckDB and `pytest`, then the full project suite. A separate shadow job merges only the exact recorded colleague #163 holding and #147 research heads into the ephemeral runner checkout and executes the same tests. No PR, branch or production merger is performed by the workflow. Mark process-recovery PASS only after observing the corresponding jobs' conclusions and logs.

Passing these tests proves only these specific fault/backup conditions in GitHub-hosted Linux. It does **not** establish durability under physical power cut, OS disk cache loss/fsync, hardware corruption, independent trusted timestamp/provider receipt, continuous exchange calendar, authenticated future outcomes, multiprocess queue semantics, remote replication, production restore runbook or fully reviewed migration. Database write auditing still requires an independent reviewer; release remains fail-closed. 
