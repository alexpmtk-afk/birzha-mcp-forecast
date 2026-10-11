# G2 — End-to-end technical acceptance and release closure map — 2026-10-11

## Why "all tests green" is not a finished release

The canonical machine release policy is `docs/G2_RELEASE_GATE_20261010.json`, validated by `scripts/g2_release_gate.py`. As of 2026-10-11 the last fully tested Draft line is #197 at `4ca38bd8473dab9ad107e5735502131ff32a88bc`, **1,589 tests PASS** in ephemeral CI #38138653948, including the exact #168/#179/#147 integration and no push to main. This newer functionality is **not merged into main or deployed**. The canonical policy still reports **12 blockers**: 10 technical/evidence/acceptance requirements, plus 2 explicit permissions. Do not mark any of them PASS solely from adding yet another test or report.

## New combined end-to-end acceptance in this PR

`tests/test_g2_end_to_end_release_acceptance.py` connects previously separate pieces in ONE synthetic, disposable, closed-loop test:

1. Fabricate raw-source D1/H1/M1 receipt pages with a consistent historical D1 activity witness. Issue a single prospective **staging only** SBER Forecast through the source-bound real-issuance *code path*, with source/calendar hash and T0 guards.
2. Append an original versioned official-schedule document candidate with two amendments; check original SHA, exact SECID/board, first future event and assigned trading day. The documents are **synthetic** and not actual authenticated MOEX pages.
3. Add a nonce-bearing RFC3161 proof structure via a **mocked** OpenSSL runner and synthetic TSA response. Verify immutable Forecast/source/manifest binding, but NEVER mark this mock as external trusted time.
4. Close every writer, create cold backup of the **entire DuckDB file**, restore to a separate disposable directory and re-open. Require identical Forecast + source page receipt + history page receipt + schedule document + TSA artifacts; no actual outcomes fabricated; no user-authorized production.
5. Prove a calendar SECID mismatch refuses issuance before creating ANY file. Confirm release-gate still has 12 blockers.

This is meaningful integration/regression and full cold-restore evidence but **not** physical power-loss certification, independent live provider attestation, future/out-of-sample forecasting skill or HOME acceptance. A test-only mock TSA must never be cited as genuine proof of time.

## Exact machine blockers, evidence needed, and clear disposition

| Gate | Status on pinned manifest | Concrete final acceptance evidence |
|---|---|---|
| code_three_line_compatibility | **PASS**, earlier pinned baseline; needs final current-head rerun | Current exact SHA pinned #168/#179/#147 + stacked new Draft HEAD ephemeral no-push full pytest/compile/security and regression |
| individual_pr_semantic_review | OPEN | Independent full-domain review of exact new combined HEAD, risk/security/T0 and data semantics, comments resolved, recorded reviewer approval |
| historical_first_receipt_attestation | BLOCKED | Original first-seen receipts for legacy historical rows **cannot be retroactively recovered**. Legacy must remain DEVELOPMENT-only. A separate explicit *prospective-only production scope/policy decision* is needed if historical attestation is structurally impossible |
| future_outcomes_5_10_20 | BLOCKED | Genuine source-bound future candles and independently checked actual sessions after each real forecast T0; mature +5/+10/+20 horizons naturally, no filling from past unseen windows |
| independent_exchange_calendar_and_clock | BLOCKED | Independently authenticated exact SECID session schedule (with amendments and exceptions), trusted source/receipt UTC uncertainty, **genuine** approved RFC3161 TSA token signed before independently verified first future event |
| forecast_skill_and_oos_significance | BLOCKED | Pre-registered evaluation, enough matured prospective outcomes, confidence limits, baseline comparison, untouched protected 2023–24 holdout opened only by separate agreed formal review |
| forecast_protocol08_complete | OPEN | Implement and accept the real Control/Route/scenario/quality contracts; never synthesize claims from baseline facts or backfill invented signals |
| durable_single_canonical_storage | OPEN | Explicit authorized target storage contract, migration/cutover plan, complete change/restart/cold restore+integrity in isolated system, eventual production recovery proof without destroying original data |
| home_windows_readonly_acceptance | BLOCKED | Resolve the Windows approval/PSSecurityException legitimately, via authorized read-only diagnostic route, confirm actual HOME branch/dirty status, runtime source/DB, compatible versions. **No PowerShell execution-policy bypass** |
| live_forecast_issuance_after_source_receipt | BLOCKED | Trace *every* HOME real LIVE producer to source-bound fresh T0; verify exact raw provider receipts and negative pre-T0/clock skew cases on the real active path, not merely synthetic staging |
| production_backup_rollback_and_authorization | OPEN | Dry-run backup/restore and rollback for **exact intended deployment**, independently supervised recovery proof, explicit permissions and prepared recovery runbook |
| user_approved_main_merge | NOT GRANTED | Separate explicit instruction naming exactly reviewed SHA/PR, after signed-off code/policy acceptance |
| user_approved_home_deploy | NOT GRANTED | Separate explicit instruction naming target HOME and rollback/backup plan, after readiness |

The two permissions must remain separate. An instruction to continue analysis is not an implicit approval to merge/deploy, mutate production databases, access secrets or perform automated trading.

## Genuine finish line, not an endless sequence of PRs

- **A: Complete nonproduction implementation** — all key logical components integrated into one reviewed canonical branch, one full end-to-end acceptance & cold restore, no unresolved technical/security regressions, no fabricated historical receipts.
- **B: Independently evidenced prospective pilot** — exact fresh source capture on a permitted market, authenticated external timestamp before *first future evaluation event*, official schedule amended and checked, all original proofs durably retained. No old forecast retrospectively labeled prospective.
- **C: Objective measurement** — actual mature +5/+10/+20 sessions, registered metrics and significance before protected OOS access, honest confidence level and quality criteria.
- **D: Explicit reviewed release** — reviewer signoff, approved production-only backup/restore, HOME read-only acceptance, explicit authorization for main merge and separate permission to deploy. Defer live trading entirely unless separately authorized.

External trust approval, legal/provider permissions, future time passage and user merge/deploy permissions are **real external blockers**. They cannot be solved by repeatedly adding self-signed dummy receipts or relabeling an OPEN gate.

No new genuine MOEX/TSA requests or transmission of hashes occur in this integration test. Main, HOME, protected holdout, production DB, secrets and automated trading remain untouched.
