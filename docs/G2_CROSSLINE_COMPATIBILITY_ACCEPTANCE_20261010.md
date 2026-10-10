# G2 crossline compatibility — combined code acceptance, 10 October 2026

## Scope and independent evidence

This report is a **read-only integration verification**, not a request to merge or deploy. The forecast/prospective branch HEAD `282497d5d3e43b35277856f84767faf834a98f59` (PR #148) and the Codex-colleague regime-research HEAD `82af27e1f9d79f02f8062c5756047fe1fb35ef20` (PR #147) share accepted D1 research ancestor #137 (`36ac2f9a6c7d28838e3e863b613a574be37d8fa6`).

GitHub compare of #137 → #147 lists **18 changed paths**. Compare of #137 → #148 lists **27 changed paths**. Intersection: **0**. This shows no same-path editing conflict but is not by itself proof of Python compatibility.

The new .github/workflows/g2-crossline-shadow-compat.yml creates a disposable combination in a **GitHub-hosted Ubuntu Actions runner**:
- pin forecast line ancestry at exact #148 SHA;
- fetch the colleague's exact #147 branch commit and refuse the test if its head moves;
- execute `git merge --no-commit --no-ff` inside that runner **without pushing or creating a merge commit**;
- run `python -m pip install -e '.[dev]'`, full `python -m pytest -q`, compile the joined modules;
- read-only permissions, no secrets, no HOME, no production access, no trading.

Proof: [GitHub Actions run 38068213118](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38068213118): ephemeral merge PASS, **787 pytest PASS (20.63s)**, Python compile PASS; Repository Preflight and Public Repository Security PASS for the CI PR commit. No conflict paths, no production writes.

## Strict limits and independent review

A green joint test is **technical regression compatibility only**. It is not independent expert code review and cannot certify correct causal provenance, financial forecast usefulness, independent OOS accuracy, live outcome completeness or release readiness. In particular:
- PRs #132–#148 are still staged DRAFT and unmerged; their GitHub base branches follow a specific dependency chain. No auto-merge or bulk merge is authorized.
- Historical `first-seen` missing in old archives cannot be reconstructed: Stage B PR #133/#134/#135 implements explicit receipt modes and opt-in decision cutoff, but strict replay cannot be certified from legacy rows with unknown observed-at. Market metadata historical vintage/flow inputs remain separate gaps.
- D1 research in 2021–2022 remains `RECONSTRUCTED_RESEARCH_ONLY`. The reserved 2023–2024 period has not been proven independently untouched. Colleague's regime descriptive results are not established predictive power.
- Six genuine-source baseline forecasts frozen 2026-10-10 ~15:15 UTC on exact SECID are `UNVALIDATED`, `DEGRADED`, and their 5/10/20 **real future trading sessions have not elapsed**. All 18 future outcome statuses remain PENDING. An independently verified exchange calendar and durable trusted first-receipt provenance are still outstanding.
- The staging pilot keeps original receipts in SQLite sidecar and canonical Forecast/Outcome in DuckDB; recovery tests PASS but there is **no multi-database atomic commit**.
- `HOME-READINESS` on the actual Windows computer is still unverified; GitHub-hosted CI is **not** a HOME health/restart or production DB validation.
- Roadmap has 24 main blocks with only 9 listed ready (as of current 10 Oct sheet). Detailed mechanism sheet contains 72 parts, only 16 ready. The remaining Protocol 08 Control/Route/Scenarios, independent validation, tradability, execution/P&L and portfolio layer are NOT all implemented.

## Practical acceptance and minimal next steps

**PASS:** exact two-line no-push transient merge; 787 combined tests; crossline changed-path overlap 0; isolation from HOME/production.

**PARTIAL:** G2-DATA-FIXES has bounded validated code under PRs #133/#134/#135 plus research-feature contract #136, but independent semantic review and true proven source vintage remain open. No blanket strict-PIT or production recommendation.

**BLOCKED by actual future time:** 18 outcomes on 10 Oct 2026 are PENDING until genuinely completed 5/10/20 exchange sessions with exact contract evidence. Do not backfill or fabricate outcomes. Do not touch the reserved 2023–2024 holdout without its separate independence gate.

**Next bounded engineering step:** (i) targeted independent read-through of data-fixes contracts #133–#135 with explicit negative tests and a red/yellow/green acceptance matrix, (ii) read-only HOME readiness via an authorized HOME connector when available, and (iii) prepare exact post-maturity source acquisition separately; no automatic daily production scheduling or merge until explicit authorization. Run the shadow CI again only if either HEAD changes.

No merges, deployments, model-fitting based on future outcomes or production/database changes occurred in this audit.
