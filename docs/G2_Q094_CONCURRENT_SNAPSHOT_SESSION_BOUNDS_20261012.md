# Q-094 — multi-connection M1 source snapshot and session edge checks

Based on Draft #214 (Q-093); entirely opt-in offline G2 research.

## Fixed data-race boundary

Previously original_capture_handoff read SQLite in several SELECT operations
without a single transaction: audit(), pages() (which audits a second time),
and separate metadata receipt SELECT. A different SQLite writer could
commit between those reads, producing a manifest describing more source
receipts than the original pages used to rebuild a M15. That is a causal
breach even if each record has a valid local SHA chain.

The handoff now begins ONE read transaction, verifies its source chain
and derives source-page bodies, receipt metadata and downstream Q-088
preflight consistently within the same SQLite snapshot, then closes it
with ROLLBACK on either success or validation failure. It does not
introduce any database write in this read-only pathway. An unexpected
preexisting transaction causes fail-closed refusal.

## Adversarial acceptance

- Two SQLite connections race with identical capture_key, instrument,
  page offset, original source bytes and metadata: one new record and one
  identical duplicate, with just one committed receipt.
- Two competing source variants for the same id: one new record and one
  Collision; no overwrite or source mixing.
- A different connection tries to add a page *during* M15 preflight:
  result must remain bound to one read snapshot and exactly one original
  body/receipt, regardless of whether the writer blocks (rollback journal)
  or concurrently commits (WAL).
- Failed downstream preflight rolls back its read snapshot, allowing later
  valid new original intake.
- Strict boundary tests show the requested 15-minute M15 must lie wholly
  within the single **claimed** session interval, down to the second/
  microsecond. Boundary equality is allowed but does not upgrade confidence.

## Non-claims and blockers

Even a consistent, crash-safe SQLite research manifest cannot verify
the operator-supplied official session notice, MOEX TLS origin, independent
network first receipt, trusted wall clock or real RFC3161 attestation.
A single claimed session range cannot prove absence of intraday breaks.
The 12 G2 release blockers, including two user permissions, stay OPEN.
No live HTTP/TSA calls, trading, main/HOME merge, production, Forecast V1
or protected OOS changes.

Q-093 and Q-094 are separate Draft PRs so each exact-head CI run can
demonstrate the corresponding boundary before any further feature work.
