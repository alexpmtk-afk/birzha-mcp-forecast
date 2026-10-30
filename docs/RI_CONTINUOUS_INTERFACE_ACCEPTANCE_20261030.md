# RI continuous interface and HOME acceptance — 30.10.2026

## Scope
Base main: 31d88d9cb6f6c0310f7634432099c86ad45352a4.
New read-only MCP tool market.continuous_series(payload) builds an aligned D1/H1/M15 view from caller-supplied exact-contract bars and calendar/decision evidence. It makes no market requests and writes no database or Sheet. This is a bounded analysis interface, not an automatic whole-archive fetcher.

## Inputs
root=RI; explicit coordinate RAW_CONTRACT/FORWARD_SPREAD/ADDITIVE_BACK/RATIO_BACK; timezone-aware as_of.
sessions: ascending effective_session, latest previous decision_session, decision_cutoff, decision_rows. Each row has SECID, ASSETCODE, TRADEDATE, LASTTRADEDATE, VOLUME, CLOSE, completed_at, available_at, source_sha256.
bars: secid, timeframe, trade_session_date, timezone-aware begin/end, completed, open/high/low/close, volume, source_sha256. Preserve optional original observation-quality fields; sparse data is not silently promoted.
At most 367 sessions and 50,000 source bars. Every supplied session must contain all three periods of the selected contract. No fill or alternate-contract fallback.

## Mechanics
Reuse providers/continuous_futures.py select_completed_session_volume. One decision per trading session across all periods. Roll basis=new prior close-old prior close, ratio=new/old. Source candles remain unmodified and each derived candle includes raw_ohlc. Volume remains exact-contract; original value/turnover are not price-coordinate transforms.
Only switch events effective by as_of participate. Refuse missing overlap, late/future decisions, decision after target bar start, expired/missing candidates, duplicate source keys, wrong root, absent periods and contract re-entry requiring review.
Returned hashes bind supplied content. They do NOT attest original source, official calendar or actual first publication. Output explicitly has forecast_allowed=false, calendar_status=CALLER_SUPPLIED_NOT_ATTESTED, source_availability=CALLER_ASSERTED_RECONSTRUCTED_NOT_ATTESTED. This endpoint is not passed silently into the existing Forecast Engine.

## Verification
25 targeted continuous/causal resolver tests passed; full repository suite: 542 passed (Python3.13 Windows, disposable test databases; no HOME database). Existing test runner defaults /tmp were unsuitable on Windows; scoped environment used in-memory test stores. Future cutoff/prefix invariance, additive vs forward, ratio, volume preservation and bad evidence covered.
No complete 2026 RI real-data adjusted-series acceptance is claimed: collection and official session/overlap proof remain with the existing RI task.

## Actual HOME deployment
Using the existing local controller, main 31d88d9cb6f6c0310f7634432099c86ad45352a4 was installed on 30.10.2026.
Previous version fc2572d174be435647fb6a5ea95a5b1611d3d74a.
Controller status and smoke succeeded; 43 tools. Six Forecast payloads unchanged before/after (SHA256 517548d5edd139d8edba115cf640a9cdc510da8ab41e6f66b98fea95de7658ec); workflow count8 unchanged. The deployment saved a rollback copy.
This proves installation of existing main including T-1/V3 changes, NOT installation of this new unmerged endpoint.
Private controller outputs and backup paths stay local and are not copied into this public repository.

## Model3 audit
Read canonical Project Map → «модель 3 Пурнов» A1:H94 on 30.10.2026. Revision P3_PURNOV_BAR_RESEARCH_v0.1 explicitly DESIGN_ONLY. Runtime implementation not found in main src/tests. Legacy M3_REGULARIZED_DIRECTION is a different model.
Rules P3-053/058/060/064/074 require versioned pivots, confirmation/expiry, risk/horizons and a frozen parameter manifest; concrete numeric choices not supplied by the accepted specification. No fabricated defaults or model implementation claim.
Consequently a test of actual installed Purnov forecasts is BLOCKED_NO_MODEL_AND_APPROVED_PARAMETERS. This is a concrete audit outcome, not a claim to have tested forecasting performance.
Next authorized development needs a complete experimental parameter contract and actual Purnov module; isolated research comparisons can be planned, but do not activate trading or retrospectively invent PIT.
