# G2-D1-DATASET — six-market Development extractor, 10.10.2026

**Purpose:** produce actual reproducible Development research rows and exclusions, not claim forecast quality. Stacked draft on PR #136. This does NOT modify the archive or HOME.

## Invocation

`python -m scripts.g2_d1_development_dataset --archive <copy of birzha_history.duckdb> --warmup-v2 <V2 JSON> --output-dir <new research scratch directory>`

Source opened `duckdb.connect(path, read_only=True)` with no schema migration. SHA-256 before and after must match. Output is three deterministic plain-text artifacts: `g2_d1_development_rows.jsonl`, `g2_d1_exclusions.jsonl`, `g2_d1_manifest.json`. Never overwrite a different existing artifact.

## Admission criteria

- Scope **only 2021–2022 exposed Development**; excludes reserved 2023–2024 and performance-exposed 2025+. Six markets mandatory.
- Original archived `historical_sessions` D1 calendar key follows `HistoricalDataService._verification_symbol`: futures roots `Si/BR/GOLD` use `<market>#ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V2_ACTIVITY`; `SBER/IMOEX/RTSI` use `<market>#D1_SESSION_V2_ACTIVITY` (no rolling-history component). Check the corresponding `historical_session_verified_ranges`; never silently fall back to legacy unversioned keys or infer missing sessions. This archived calendar remains research-only, NOT verified historical knowledge.
- A candidate D1 trading date uses exactly 21 **consecutive recorded active sessions**, within a single contract. For the beginning of an active futures segment, exactly the necessary preactive days come from the immutable V2 manifest's 20 `RECONSTRUCTED_MOEX` dates/SECID-specific key. Equities and indices have no inferred preactive schedule.
- Exact SECID/candle dates and count, including exclusion of any unexpected date within the candidate's window; completed, finite, valid OHLC; right market asset class (GOLD = GD futures, never equity GOLD). Missing or unusual sessions do not get compressed/removed.
- D1 formula `G2_D1_RESEARCH_SMA_TR14_D20_ER20_W20_V1`; enforce ATR14 SMA-TR, D20/ER20/W20 AVAILABLE; SMA50 remains insufficient at 21 bars. Volume/OI/Profile/Tradestats explicitly status-driven and never used as zero fill.
- Each row preserves market, SECID, event end, null historical decision timestamp (NOT_PROVEN), origin+key and all 21 expected sessions, feature contract version and values. Separate JSONL enumerates exclusion reason for **every** rejected candidate.
- Manifest records per-market candidate/admitted/excluded counters, source/V2/script/formula SHA, byte SHA for both JSONL files and explicit RECONSTRUCTED-only limitations.

## Acceptance

Independent review required before calling output admitted for calibration, plus one local read-only run against the existing archived copy and V2 evidence, and SHA/row-count revalidation. This script alone and synthetic CI do not prove the historical source calendar is complete, or that price/version was actually known on T0.

No merge/production DB edits or enabling strict forecast path. The resulting genuine dataset, not this script, is the next evidence gate into TREND/BALANCE research and bounded first forecast experiment.

## Real-archive integration failure, 10.10.2026

The first isolated local run at commit `d26dfb9786ea6ae9b9d18f29203240e2b77f3f00` stopped at SBER with `no archived active-root calendar`. Root cause in the extractor: the futures-only rolling namespace was applied to equities/indices; synthetic fixtures used the same erroneous helper and missed the mismatch. The market-aware namespace and an explicit six-market regression assertion have since been added to PR #137. This code correction does **not** yet establish that the old 18.09.2026 archive contains the required verified session rows. Rerun against the unchanged SHA-pinned archive and V2 report; if any market still lacks a proven calendar, return exact read-only `historical_sessions` and `historical_session_verified_ranges` key counts without guessing, rebuilding or changing the archive. Dataset acceptance remains pending until real artifacts are produced and independently verified.
