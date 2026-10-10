# G2 — Independent release-readiness matrix (2026-10-10)

**Scope:** release decision audit, NOT a merge, deployment, HOME maintenance, production database update, trading signal or forecast-quality claim. Owner: ChatGPT independently. Codex-colleague's TREND/BALANCE/production-feature branches are read-only dependencies.

## Factual repository state

At the time of the audit, `main` is still commit `b6c085db18d1811f46077a447e6a2251b2df85f0`; verified with GitHub compare (`main` IDENTICAL to this commit, zero ahead/behind). PRs #132–#152 are **all Open/Draft**, not merged. A stacked Draft PR is not automatically releasable even when its *own* GitHub checks are green.

| Line | PRs in dependency order | Last pinned HEAD | Verification scope |
|---|---|---|---|
| Shared infrastructure/data backbone | #132 → #133 → #134 → #135 → #136 → #137 | `36ac2f9a6c7d28838e3e863b613a574be37d8fa6` | Historical version/cutoff, D1 research; not complete historical PIT |
| Descriptive regime research | #137 → #138 → #140 → #142 → #144 → #147 | `82af27e1f9d79f02f8062c5756047fe1fb35ef20` | Research DEVELOPMENT experiment; no independent prediction quality |
| Source and prospective staging | #137 → #139 → #141 → #143 → #145 → #146 → #148 | `282497d5d3e43b35277856f84767faf834a98f59` | Real-source frozen, uncalibrated six ForecastRecords; 18 future outcomes PENDING |
| Production-price feature corrections | #148 → #149 → #151 | `0fdba4b057cee4f249751e2dcc7b9c6be1f9a0ae` | Exact window/capability/feature-version rules; not model quality |
| Historical revision integrity correction | #148 → #152 | `e105afeda4828ec3116a2e1ec6431130a11743dc` | Original serialized revision payload hash, causal cutoff; legacy base raw bytes unavailable |
| CI compatibility-only helper | #148 → #150 | `70bcc84c5301ee9167c02ac7723491c07d57aa32` | Only test workflow/report; **not required to merge as runtime code** |

The later #149 and #151 were created after the earlier two-line compatibility run. They extend production features and must be included in release CI. GitHub compare for #148 → #151 changed 14 paths; #148 → #152 changed 5 paths; intersection **zero**. #137 → #147 changed 18 different research paths. This is a structural observation, not a substitute for joint pytest.

## CI and evidence at audit start

- PR #147 research: archived descriptive results and CI; no actual future forecast effectiveness established.
- PR #148 forecast/outcome reader: **523 pytest PASS** on its recorded HEAD; six actual-source frozen forecasts preserved in disposable ledgers.
- PR #149 production window: **576 full pytest PASS**, [Actions 38066043715](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38066043715).
- PR #151 production feature versioning: **630 full pytest PASS**, [Actions 38068704468](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38068704468).
- PR #152 revision correction: **528 own-line pytest PASS**, [Actions 38069237397](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38069237397); previous two-line shadow merge with #147 **792 combined PASS**, [run 38069237422](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38069237422).
- **Three-line gate independently verified:** [GitHub Actions 38069916800](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38069916800) on CI-only commit `b4487b395b39e0747a5bf06471b6f6288c8ff072`: exact HEADs #152 + #151 + #147 merged **twice only inside isolated GitHub runner**, 144 targeted tests PASS (4.81s), **910 full tests PASS (41.41s)**, compile PASS and no push/production action, Security/Preflight PASS. The very same run explicitly printed `G2_RELEASE_ALLOWED=false`; only the code regression gate becomes PASS.

## Formal release-decision matrix

| Gate | Current state | Blocking reason / proof to obtain |
|---|---|---|
| Three-line source/forecast/feature/research code regression | **PASS** | GitHub Actions [38069916800](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38069916800): 144 focused/910 full pytest, exact three HEADs, ephemeral 2 merge commits **not pushed**, compile/Preflight/Security PASS |
| Independent human/architectural review of all affected commits | OPEN | Human reviewer approval of causality, interfaces and recovered failure paths, not just test count |
| True historical original first receipt and provider metadata vintage | BLOCKED | Legacy DuckDB base row stores no original raw JSON bytes/attested historical receipt; cannot reconstruct past PIT |
| Matured post-forecast +5/+10/+20 genuine sessions | BLOCKED | As of 10 October 2026, future sessions have not occurred; 18 outcomes must remain PENDING |
| Independent exact-SECID exchange calendar and trusted time provenance | BLOCKED | HTTPS Date/runner clock not signed external attestation; no independent complete session list |
| Predictive skill, OOS and statistical robustness | BLOCKED | No truly matured outcomes or validated untouched OOS selection; 2023–24 protected holdout not opened in this audit |
| Full Protocol 08, Control/Routes/Scenarios/Risk/economic execution | OPEN | Comprehensive mechanics backlog remains, not satisfied by baseline output |
| Canonical durable append/atomic outbox | OPEN | SQLite staging receipt and DuckDB canonical records remain separate transaction domains |
| Read-only HOME Windows acceptance of actual current environment | OPEN | No independently executed HOME health check from this chat; CI in Ubuntu cannot substitute for it |
| Release rollback, backup and explicit user approval | OPEN | Not verified or authorized; do not alter main, HOME, production database or secrets |

## Release rule

The machine-verifiable source of this decision is `docs/G2_RELEASE_GATE_20261010.json`, validated by `python -m scripts.g2_release_gate --manifest docs/G2_RELEASE_GATE_20261010.json`. `--require-release` MUST exit nonzero while a blocker exists. CI can pass while `release_allowed=false` — this is correct.

Release order in a future explicitly approved process would follow parent/child dependency and require review, not a single bulk merge: #132–#137 shared base first; then selected runtime source branch #139/#141/#143/#145/#146/#148; then #149/#151 and #152 reconciled; research branch #138/#140/#142/#144/#147 added separately **only if its integration is desired and reviewed**. CI-helper #150 or this release-gate PR need not become part of runtime. Re-run all combined tests after *any* head shift. Check true Windows HOME state, backup/rollback, live source latency, and source calendars before deployment.

## Important interpretation

**Technical code regression PASS does not mean full G2/P08 project completed.** Never retrofit signed timestamps onto archived development rows or evaluate a first-touch protocol with only horizon-end close. Existing ForecastRecords issued 10 October are immutable. Later actual observations belong in a separately sourced append-only Outcome only after verified maturity. No third-party/source plugin bridge or working local Codex was used to create this report.

**No merges, deploys, production reads/writes or protected OOS data access were performed.**

## After three-line gate PASS

The gate report in `G2_RELEASE_GATE_20261010.json` now records **code_three_line_compatibility: PASS** and run 38069916800. This **does not** close the nine other factual release gates, and main/HOME merge/deploy approvals remain false: 11 release blockers including the two user approvals. The manifest's next CI run validates this preserved fail-closed outcome. The first 10 October frozen uncalibrated forecasts retain 18 PENDING outcomes and are not modified by the release audit.

## Independent causal-semantic follow-up: event time is NOT publication time

On the combined #151 production-feature semantics, `MarketSnapshotService.build_with_instrument()` takes `max(last_ends)` as its **normal** non-historical `causal_t0`. `ForecastService.build()` forwards that snapshot into `build_forecast_from_snapshot()`, whose `ForecastRecord.created_at_t0` is `snapshot.as_of`. This timestamp identifies the **latest completed source event**, not necessarily *when the forecast was actually issued* or when all its input bytes were first received. For example, an M15 candle that ended 13:14:59 Moscow may be first acquired at 14:00 Moscow. Labelling a newly issued forecast with 13:14:59 would be retrospectively backdated and can lack an ISO timezone offset. This default normal/reconstructed path **must not be directly wired to production prospective journaling**.

The already existing `ProspectivePilotLedger.capture()` fail-closes `latest_completed_event_end <= source_observed_at <= record.created_at_t0 <= capture_time`, and the 10 October real-source-only adapter #146 deliberately sets issue T0 from the actual runner clock **after** all original provider pages were observed. The new synthetic negative suite `tests/test_g2_live_issuance_semantics.py` verifies the default event-time behavior, refusal of naive/backdated issuance and shape-only acceptance of a truly later receipt T0. It does NOT claim cryptographic authenticity for synthetic receipts; a production adapter and independent external timestamp/secure calendar remain outstanding.

New mandatory release gate `live_forecast_issuance_after_source_receipt` is `BLOCKED` until all default LIVE callers are proven to route through this strict issued-at + full SHA receipt contract. The protected HOME runner accepted a 10 October diagnostic but the actual read-only checks could **not execute**: `pwsh` was missing in run [38070736976](https://github.com/alexpmtk-afk/gpt-powershell-bridge/actions/runs/38070736976), then Windows execution policy blocked PowerShell .ps1 before any audit commands in [38070781907](https://github.com/alexpmtk-afk/gpt-powershell-bridge/actions/runs/38070781907). This is **not** a negative health check of the Birzha repo; it is a protected external execution-path blocker. Do not change execution policy or use alternate shells to circumvent it. Draft HOME audit evidence: [private bridge PR #104](https://github.com/alexpmtk-afk/gpt-powershell-bridge/pull/104).

Accordingly there are now **12 release blockers**: 10 unfinished/blocked technical or evidence gates (including live first-issuance T0 and HOME) plus 2 explicit user approvals. A green combined CI cannot make `release_allowed=true`.

## 10 October 2026 — latest current-head manifest reconciliation

The early triple-line audit recorded production feature HEAD **PR #151**, but the colleague subsequently finished two additional dependent Draft PRs: **#154 baseline decision/abstention**, and **#155 price-level source admission**. A **separate no-push compatibility run** [Actions 38078049456](https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38078049456) has now actually checked:

- ChatGPT source-bound first issuance **PR #156** HEAD `5f091403510a7af0a4c036404db7c04af959120d` (includes revisions #152 and release-diagnostics #153, after its parent #148).
- Colleague's production/decision/price-level **PR #155** HEAD `0a55a9607642cd1428a396c9d69ff404c7d0578f` (parent #154 → #151 → #149 → #148).
- Colleague's regime research **PR #147** HEAD `82af27e1f9d79f02f8062c5756047fe1fb35ef20` (parent #144 → #142 → #140 → #138 → #137).

**Result:** exact three-head local GitHub runner-only merges, 106 targeted pytest PASS and **999 full pytest PASS** (39.54s), compilation + Security/Preflight PASS; all within ephemeral Actions runner, no repository push/merge or production operation.

The machine-readable release manifest `G2_RELEASE_GATE_20261010.json` has now been **re-pinned to exactly #156/#155/#147**, and its DAG records new #153/#154/#155/#156 dependencies. `scripts/g2_release_gate.py` validates the actual new leaves, rejects outdated PR #151 as the release leaf and enforces the dependency chains. The release remains **BLOCKED** with the same **12 requirements** (10 factual engineering/evidence gates and 2 explicit user approvals). A full latest-head manifest+code CI rerun is required on the final PR HEAD.

Importantly, **compatibility PASS does not imply source attribution, original provider time proof, model calibration, healthy HOME runtime, completed 5/10/20 future outcomes, or readiness for production**. No protected holdout data used.
