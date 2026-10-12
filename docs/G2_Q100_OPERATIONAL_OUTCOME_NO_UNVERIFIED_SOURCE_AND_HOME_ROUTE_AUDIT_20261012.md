# Q-100 — Close the unsafe LIVE Outcome entrypoint; audit HOME permission

Base: Q-099 Draft #218 exact commit aa5589dc0a8c1879a86c21e8349f9fdcd3eebbf0.
This is a narrow research-branch correction, not a merge or production action.

## Confirmed operational side effect

The real MCP outcome.evaluate previously called
OutcomeService.evaluate(forecast_id, evaluation_date). That service:
1. Read any canonical historical Forecast Record (no independent prospective
   source receipt or issued-at T0 authenticity check).
2. Resolved the contract, fetched completed D1 rows from MOEX/MarketData,
   and counted rows as +5/+10/+20 future sessions. There was no independent
   exact SECID official-calendar witness or proof of source receipt time.
3. Built HorizonOutcome and called DuckDBOutcomeJournal.append, making
   the result appear as a canonical forecast outcome.

A development-only historical D1 calculation is NOT the accepted
future-outcomes_5_10_20 release-gate evidence; missing or synthetic dates
and a caller-selected evaluation date cannot certify prospective maturity.
This is a concrete operational data-integrity risk, not just a label.

## Minimal fail-closed change

New OutcomeService.operational_evaluate refuses live admission before ANY
forecast journal read, MOEX resolver/candle call, Outcome Journal read or
append. It provides a typed BLOCKED result with zero future sessions and
zero writes, reasons, and explicit false provenance/calendaring flags.
MCP outcome.evaluate uses exactly this protected route and its description
matches actual behavior. The former calculate/write evaluate() remains
available only as a clearly named DEVELOPMENT-only legacy API for
offline fixture/historical math regression, not as the MCP runtime writer.

Negative spies throw if forecast read, future fetch or canonical append
is attempted. Invalid IDs are rejected before any source access.
This prevents contamination of the currently connected operational
Outcome Journal with unverified future outcomes.

## HOME protected bridge read-only review (no HOME calls)

Source: PRIVATE repository alexpmtk-afk/gpt-powershell-bridge
Draft PR #104 head 76bf4de1325b66e201a05c505ccdaff01d59de85.
The typed .github/workflows/birzha-control-pr-gateway.yml currently
allows repo_status but ALSO deploy_main, rollback_last, start/stop/restart,
mcp_call and other operations. Its job's PowerShell shell is
powershell -NoProfile -ExecutionPolicy Bypass -File "{0}".
This route CANNOT be treated as a sanctioned exclusively read-only HOME
diagnostic simply because repo_status is one allowed operation.
Separate owner/security approval and least-privilege route review are
required; NO workflow was executed, no queue request created and no
ExecutionPolicy was altered or bypassed.

Existing Windows diagnostic errors remain:
- pwsh unavailable at tested runner
- native PowerShell workflow blocked before intended diagnostic due to
  execution policy (PSSecurityException)
Thus no HOME branch/SHA/dirty/database/disk/runtime information was
independently measured in this step.

## Actual Protocol 08 and release gates

The existing g2_protocol08_semantic_gate confirms missing independent
Control confidence/holding, Route obstacles and confidence, approved
Alignment 0-15 allocation, Market State classification and live
first-receipt-anchored issued T0. No caller-assigned value or staging
fixture can close these product and external-evidence requirements.
The canonical docs/G2_RELEASE_GATE_20261010.json remains unchanged:
10 technical/evidence blockers + 2 explicit user approvals.
This task reduces the operational risk of falsely writing Outcomes; it
does NOT move any blocker to PASS.

Requirements for a future operational Outcome writer: approved authentic
original future D1 bytes and receipts, independently verified exact-SECID
session calendar including breaks/holidays/amendments, causal clock and
forecast T0, retained complete 5/10/20 session windows, proven
idempotency and atomic durable canonical journal, permissions and expert
review. No backfilled original old source receipts.

No main merge, HOME/REMOTE command, production writes, automated trades,
holdout 2023–24 access, release policy change or forecast scoring change.
