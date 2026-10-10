# G2 — Independent retrospective audit of previous stages (2026-10-11)

## Scope, source of facts

Reviewed the live GitHub Draft lines #147/#156/#168/#170/#172/#173/#174/#176/#177/#180 and the newer colleague #175/#178/#179, machine release manifest and existing Windows bridge PR #104. All previous PASS claims were assessed at their **actual** pinned HEAD and CI scope. Local HOME, production, keys, DB and protected 2023–24 data were **not accessed**. Only an isolated new GitHub Draft branch is changed.

## Findings and minimal fixes

| ID | Code-level evidence | Original regression hole | Narrow correction | Status |
|---|---|---|---|---|
| G2-AUDIT-01 | `scripts/g2_issue_sourcebound_atomic_staging.py` rejected any new issuance when `audit()["outcomes"] != 0` | First prospective Outcome stored for a different earlier Forecast made per-SECID storage permanently one-use, even for new source bytes and a later T0 | Retain independent prior Outcomes; for a **newly APPENDED** Forecast require only its own outcome count to be zero. Continue to audit every previous pair transactionally | FIXED in Draft; regression uses 2 Forecasts+1 prior Outcome in same DuckDB |
| G2-AUDIT-02 | Both staging issuers called `Path(...).resolve()` **before** symlink inspection; governed network capture could create its output under a symlinked HOME source directory | `resolve` erased path indirection before the guard; source directory could point to a protected area. Other listed page symlinks were insufficiently checked until page traversal | Refuse symlink components/ancestors and known HOME/production destinations before resolving, **before network writes**; require disjoint source vs storage trees and verify each listed raw page is a regular file with expected SHA | FIXED in Draft; symlink source/root/ancestor/page and HOME collector tests |
| G2-AUDIT-03 | Code integration from #180 was pinned to colleague #173 while #179 was already newer (#173→#175→#178→#179) | Green 1,295-test #180 workflow cannot certify #179; stale exact PR head in release machine | Advance exact pin and parent DAG to #179 `1097e67ae240b797e9e7d62e276745dc0bb4294d` with ancestry #175 and #178; force new local no-push joint CI for #168/#179/#147 and focused diagnostics tests | PENDING final CI |
| G2-AUDIT-04 | Old `forecast.create` and `market.analysis` share `ForecastJournalService.create_and_save`; default snapshot T0 was candle end | Operational backdated issue could be written as "ex-ante" | Draft #177 fail-closed guard remains in place; correct source-bound T0 issuer exists only in explicitly disposable #180 staging, not production MCP | PARTIAL: unsafe writes prevented, operational replacement not deployed |
| G2-AUDIT-05 | Original stored 2021–22 historical sources do not retain genuine first-receipt evidence; prospective 2026 outcomes not mature | Tests cannot reconstruct past receipts or later market outcomes | **Never fake or backfill attestation, never unlock 2023–24 holdout.** Keep appropriate release blockers | BLOCKED by factual evidence |
| G2-AUDIT-06 | HOME bridge Draft #104 hit missing pwsh and protected Windows PowerShell execution policy | No actual path/branch/SHA/data or health facts were measured | Existing typed read-only gateway assessed from GitHub but its preexisting `ExecutionPolicy Bypass` requires separate security approval; only the approval plan is prepared | BLOCKED, not attempted |
| G2-AUDIT-07 | #180 atomic storage used one DuckDB file with synthetic fixtures; no real new MOEX call/independent timestamp verification | CI green proves rollback/atomic code but cannot prove provider authenticity or production readiness | Preserve explicit `independent_provider_and_clock_attestation=false`, `strict_ex_ante_proof=false` and release-blocked | OPEN |

## Exact-head dependency and test acceptance

PR #180's last before-audit reviewed HEAD `68bd7f3d5ea2f09ba3f4272eae0410d768e6474b`, Actions `38087512439`: **282 focused/1,295 full PASS** on #168 + #173 + #147, no push. Correct, but **outdated relative to #179**. Do not label that run as proof for successor #179.

This follow-up pins #168 `93c1639a23203dc5005fdf78515053813ba8273e`, #179 `1097e67ae240b797e9e7d62e276745dc0bb4294d` (includes #175 and #178) and #147 `82af27e1f9d79f02f8062c5756047fe1fb35ef20`. CI checks branch exact SHA and relative DAG ancestry, merges **only within isolated GitHub runner without push**, and executes focused/full pytest + compile + Security/Preflight and release fail-closed checks. Independent subject-matter review remains separate.

## Safety and limits of path validation

Local checks refuse explicitly symlinked ancestors and typical HOME/production roots before HTTP writes. They do **not** make the filesystem impervious to adversarial concurrent path swaps (TOCTOU), nor constitute OS filesystem sandboxing. Production rollout would need separate permission boundaries and protected directories. No HOME diagnostic or real MOEX fetch was executed in this audit.

## Actionable backlog by risk, not by increasing test count

1. Final exact #179 joint CI and own negative regression tests. If failed, isolate and correct the specific root cause, no PASS without proof.
2. Independent semantic review of #179's six-market saved-data report and source age language; distinguish wall age vs verified source receipt.
3. Authentic **single-market** controlled MOEX receipt/run in a correctly authorized staging environment, with recorded real UTC receipt and raw body SHA. The provider/clock attestation gate is still separate even after this.
4. Approved HOME read-only route (existing gateway may use a Windows execution-policy exception and cannot be auto-activated).
5. Full Protocol 08, production backup/restore/atomic migration, future +5/+10/+20 genuine completed sessions and out-of-sample validation; explicit two user permissions for merge and deploy are last, separate requirements.

Current release manifest must remain `RELEASE_BLOCKED`; success or failure of a synthetic CI may update ONLY the code-compatibility gate, never these ten factual/approval requirements.
