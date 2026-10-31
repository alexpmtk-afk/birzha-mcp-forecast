---
name: birzha-continuous-futures
description: Audit and route MOEX exact-contract futures rollover, T−1 source selection and derived coordinate requests; distinguish GitHub source from HOME runtime.
status: source-available-home-unverified
---

# Futures rollover — versioned source rules and research gates

Before any action, read `AGENTS.md`, `docs/CHAT_DATA_OPERATING_RUNBOOK.md` and `docs/CONTINUOUS_FUTURES_POLICY_RESEARCH_20261029.md`. The GitHub T−1 resolver is implemented, but never claim installed HOME, strict PIT, or derived multi-contract OHLCV without verified runtime evidence.

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
2. Reuse the **single GitHub runtime T−1 selector** in `src/birzha/providers/continuous_futures.py` for contract selection. The current historical resolver `moex_history.py` is wired to it. Do not reintroduce same-day volume or treat V2 verification markers as valid after `ROLLING_HISTORY_V3_T_MINUS_1_EXPIRY`. Use one effective-contract ledger for every timeframe with expiry and historical-finality safeguards.
3. Log switch basis/ratio, decision session, effective trade session and exact provenance. Do not recompute this schedule separately for D1/H1/M15.
4. Derive requested coordinates **on demand** from immutable exact-contract RAW and **versioned event ledger**; no overwriting accepted source candles.
5. Profile/VWAP/Delta/OI use exact source contract. Never invent traded volume at price from M1 OHLCV.
6. Treat interval finality (`completed`), 15-minute source density (`DENSE_15_OBSERVED` vs `SPARSE_M1_UNKNOWN_GAPS`), and PIT availability as **three independent properties**. Retain every observed M1 and its volume; do not invent missing trades or silently discard sparse M15. Native H1 end timestamp shorter than one hour is **not** by itself proof of incomplete H1. Check source OHLCV against M1, and forbid Model №3 admission without a separate input-quality gate.
7. If any audit gate fails, return `PARTIAL` or `BLOCKED`, not `PASS`, and give the exact missing evidence; never silently fill missing M15 minutes.
8. Run the seven acceptance gates listed in the proposed canonical document before suggesting Forecast admission.

## Output
Return: scope and explicit environment, source manifest/SHAs, exact contract timeline, decisions/effective dates, numerical coordinate checks, D1/H1/M15/session identity, quality exclusions, PIT status, report/PR refs and outstanding gates.

## RI archive layout guard (31.10.2026)

The owner retired `README_STATUS` in the existing RI Google workbook
(`1PuPv7Nv_Dpj-KTECPHNHecj34HrR9K5y53OwRcu7RVY`).
Do not ask for it, recreate it, backfill it from old RTSI/RI copies, or
interpret its absence as a failed source/mirror operation. Old Google Drive
`DATA_TABLE_FORMAT.md` and `DATA_UPDATE_POLICY.md` still contain historical
tab lists; follow the newer explicit rule in `AGENTS.md` and the runbook.
Use the existing `SYNC_STATUS`, `QUALITY_AUDIT`, and project documentation
instead. This exception concerns RI only and never authorizes edits to
other canonical workbooks.

## Authorization
This GitHub skill and T−1 source policy are versioned in main; no installed HOME readback has been obtained. Do not imply source changes are deployed or that strict historical PIT is proven. Never mutate production DuckDB, canonical Google source cells, secrets or execution behavior without scope and independent checks.
