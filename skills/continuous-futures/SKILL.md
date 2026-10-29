---
name: continuous-futures
description: Research-only route for exact-contract MOEX futures rollover, source QA and derived analytical coordinates; not deployed.
status: proposed-not-installed
---

# Futures rollover analyst — proposal, not installed

Before any action, read `AGENTS.md`, `docs/CHAT_DATA_OPERATING_RUNBOOK.md` and `docs/CONTINUOUS_FUTURES_POLICY_RESEARCH_20261029.md`. Never treat this proposed skill as live policy or evidence of access to HOME.

## Trigger
Use for a request to combine historical sessions across multiple futures **contracts**, audit contract-selection schedule, compare Additive/Forward/Ratio, or feed cross-contract history into a forecasting model.

Do not apply to an exact SECID's ordinary **session concatenation**; use the existing `history.stored_series` mechanism. Do not apply to cash indexes/equities as futures.

## Mandatory inputs
- Root (e.g. RI or Si), exact contract SECIDs, underlying/units and expiry metadata.
- Immutable source D1/H1/M1 with independent hashes and source quality.
- Official trade-session calendar, full candidate daily volume history and explicit analysis T0.
- Availability confidence and event/algorithm versions; if source first-seen unknown, strict-PIT is blocked.
- Requested price-coordinate purpose: `RAW_CONTRACT`, `FORWARD_SPREAD`, `ADDITIVE_BACK` or `RATIO_BACK`.

## Analysis
1. Inspect available tools, installed code SHA, historical contract coverage and the active project map; do not claim production access just because GitHub is connected.
2. Use a **single T-1 causal effective-contract schedule** for every timeframe, with expiry and historical-finality safeguards.
3. Log switch basis/ratio, decision session, effective trade session and exact provenance. Do not recompute this schedule separately for D1/H1/M15.
4. Derive requested coordinates **on demand** from immutable exact-contract RAW and **versioned event ledger**; no overwriting accepted source candles.
5. Profile/VWAP/Delta/OI use exact source contract. Never invent traded volume at price from M1 OHLCV.
6. If any audit gate fails, return `PARTIAL` or `BLOCKED`, not `PASS`, and give the exact missing evidence; never silently fill missing M15 minutes.
7. Run the seven acceptance gates listed in the proposed canonical document before suggesting Forecast admission.

## Output
Return: scope and explicit environment, source manifest/SHAs, exact contract timeline, decisions/effective dates, numerical coordinate checks, D1/H1/M15/session identity, quality exclusions, PIT status, report/PR refs and outstanding gates.

## Authorization
Research, read-only verification and draft PR are not approval to merge, deploy, alter HOME/DuckDB, change Google canonical raw cells, install skills or enable trade execution. Those require separate owner authorization.
