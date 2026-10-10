# G2 — Immediate source-bound Forecast → atomic single-DuckDB staging (2026-10-11)

## Purpose

The old MCP `forecast.create` was guarded in Draft #177 because its ordinary default `ForecastService.build` stamps the **last completed event end** as if it were a LIVE issued-at instant. This new independent code provides a working **explicitly non-production** alternative. It does not quietly re-enable the MCP operation or modify the already frozen six ForecastRecords.

Two entry points:
- `scripts/g2_capture_and_issue_atomic_staging.py`: **explicit opt-in bounded network capture** of exactly **one market** via the existing `MoexIssClient` governed `_request` and resolver. Requires a fresh capture directory, a pre-existing isolated staging root and exact `SBER|Si|BR|GOLD|IMOEX|RTSI` market. Automatically attempts immediate prospective issuance after receipt.
- `scripts/g2_issue_sourcebound_atomic_staging.py`: **offline local receipt-folder issuer** for only the original pages of a fresh capture, still subject to a 300-second source→T0 freshness guard. It cannot silently rewrite or use historical/reconstructed development data.

## Enforcement

The source input must have the expected G2 real-source capture manifest schema, declared LIVE MOEX origin, one complete exact-SECID instrument, D1/H1/M1 page inventories, and all named original response-page SHA-256 values. Duplicate names, unlisted/missing pages, wrong SECID, malformed inventory and missing 50+ qualifying timeframe windows fail closed. M15 comes only from complete groups of 15 verified M1 bars. The source-bound issuer checks the raw bundle SHA and snapshot contract, earliest receipt, all completed event timestamps, snapshot-built clock and fresh issue clock; it freezes a degraded uncalibrated Forecast Record, horizons 5/10/20 PENDING.

The `SingleDuckDBProspectiveStagingJournal` stores the raw source blob, receipt evidence and immutable canonical Forecast payload **in one transaction and one DuckDB file**, with readback audit. An idempotent identical retry preserves the old Forecast. A second issuance to a new T0 with exactly the same source bytes and SECID is rejected by the caller while holding the store's lock (single process). Each market uses a distinct file under an existing disposable staging root. No HOME root, production DB, secrets, deploy, main merge, data migration or trading is permitted.

The per-market orchestration is intentional: a six-market sequential collector can make early market receipts stale before the issuance stage starts.

## Reproducible invocation (ONLY an explicitly disposable staging environment)

```bash
mkdir -p /tmp/g2-disposable-only
python scripts/g2_capture_and_issue_atomic_staging.py --market SBER --capture-dir /tmp/g2-one-time-fresh-source --staging-root /tmp/g2-disposable-only
# Or, if the real original receipts have JUST been captured:
python scripts/g2_issue_sourcebound_atomic_staging.py --market SBER --source-dir /tmp/g2-one-time-fresh-source --staging-root /tmp/g2-disposable-only
```

These commands are **documentation**, not evidence that HOME has been run. No network or HOME operation occurs in CI: all fixtures are clearly synthetic with frozen clocks. Running actual MOEX network capture has operational request quotas and must be explicitly initiated in the correct environment.

## Verified and unverified claims

Target CI checks:
- True real DuckDB transaction rollback on a fault after source receipt but before Forecast append, process restart, readback and exact duplicate.
- Source page byte mutation, missing/extra/repeated pages, wrong market and stale reception all refuse without a new Forecast.
- Single per-instrument capture orchestration with mocked collector ensures no issuing on incomplete collections.
- Existing frozen Forecast and Outcome, protected 2023–24 OOS, home services, production access and canonical data remain unchanged.

**What is NOT established**: independent signed provider receipt or local clock attestation, certified exact SECID session calendar, power-loss recovery on deployed hardware, daily production scheduling, disaster recovery, research predictive skill, real matured +5/+10/+20 Outcomes or user authorization. `strict_ex_ante_proof=false` and `production_authorized=false` are explicit outputs. A locally declared LIVE receipt verified by SHA is not a signed independent timestamp; `live_forecast_issuance_after_source_receipt` release gate therefore stays **BLOCKED** pending separate production architecture and attestation review.

HOME: private bridge Draft PR #104 confirmed Windows `pwsh` missing and `.ps1` execution denied by protected PowerShell policy **before the HOME audit**. Do not bypass, switch shells to evade policy or change execution policy here. A separately approved compliant read-only route must be established first.
