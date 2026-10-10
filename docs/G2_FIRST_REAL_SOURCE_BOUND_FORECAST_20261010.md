# G2 — First actually observed-source ForecastRecord in disposable staging

Owner: ChatGPT independently. This stacked draft derives from PR #145 (six-market raw MOEX source evidence); does not touch Codex-colleague's TREND/BALANCE.

## Contract

scripts/g2_freeze_real_forecast_from_source_receipts.py reads **only original raw provider pages** already acquired and stored by the governed six-market collector. Before every forecast, it independently rechecks manifest/page SHA, row count, exact instrument/board/SECID, candle OHLC/complete observed time, and original source receipt times. It refuses missing/unknown or tampered inputs.

D1 and H1 are taken directly from captured responses. M15 is reconstructed ONLY when all fifteen M1 candles exist in a 15-minute bucket with exact consecutive minute start times. Incomplete groups are discarded. Require **at least 50** actual completed D1, H1 and M15 bars per market. Flow is explicitly NOT_REQUESTED (no invented volume delta, OI, or TradeStats); calendar first-publication/independent attestation remains unverified.

Existing TimeframeFeatureEngine and NormalizedFeatureEngine produce MarketSnapshot feature states; existing build_forecast_from_snapshot() generates the **unmodified baseline ForecastRecord** with real first issuance time T0 set to current UTC *after* observing every input page. Snapshot warnings bind a digest of all original source bytes and label missing independent calendar/source attestation. Record is RESEARCH/STAGING, quality DEGRADED and baseline engine UNVALIDATED. No calibration or claim of price/probability skill.

Record, source-bundle bytes, actual observed-at and canonical snapshot identity are written by the already tested CanonicalProspectiveStagingBridge into disposable pilot sidecar + existing canonical DuckDBForecastJournal; the canonical DuckDBOutcomeJournal is created but contains **no future outcomes**, because +5/+10/+20 real sessions have not elapsed. Any failed source/timing/contract/50-bar gate causes FROZEN_FORECAST_REFUSED per market. A repeat cannot overwrite old records.

No HOME, production database, scheduled jobs, MCP endpoint, trading actions, 2023–2024 OOS holdout or later outcome at the time of receipt. Pilot ledger still uses two separate SQLite/DuckDB transactions; recovery is tested but not true all-DB atomicity. Receipt times are ordinary runner clocks and provider HTTP dates, NOT independent cryptographic timestamps. Public raw pages do not prove source first-receipt vintage prior to this actual run.

## Reproducible isolated invocation

Python 3.13, duckdb==1.5.5 and project dependencies:

    python -m scripts.g2_freeze_real_forecast_from_source_receipts \
      --source-dir /fresh/g2-six-market-source \
      --output-dir /fresh/g2-frozen-real-forecasts

The source directory is exactly the original six-market collector output: manifest.json plus 30 original raw provider bodies. The output contains new separate disposable canonical journal files, one ForecastRecord and a SHA/time-bound receipt per admitted market, and a top-level forecast_manifest.json. No forecast is written back to the source directory. If no market passes, process exits nonzero and preserves refusal reasons.

tests/test_g2_freeze_real_forecast_from_source_receipts.py uses **fake/synthetic market responses only** for regression: SHA tampering, stale observed-at / T0, exact M15 no-gap aggregation, 50-bar admission gate, and actual DuckDBForecastJournal/OutcomeJournal roundtrip. Tests alone cannot establish a genuine source receipt. Live acceptance requires a separate GitHub Actions run that first performs the governed real MOEX source collector, then freezes ForecastRecords immediately from the same observed bytes and uploads both artifacts for independent hash verification.

## Distinguish the three levels

1. Six-market actual raw source PASS: exact SECID, original bytes, observed timestamps, no 2023–24 holdout; independent artifact readback.
2. Real-source frozen uncalibrated ForecastRecord PASS: all required timeframes, exact close/issue T0, immutable canonical journal and receipt, **zero** future outcomes at T0.
3. True prospective **forecast-quality** gate remains PENDING until new real exchange sessions occur and authenticated exact-SECID post-capture outcomes are appended independently. Model quality, historical strict PIT, statistical significance, first-touch / intrabar excursion are NOT proven by levels 1 or 2.

No merge/deploy/HOME/production or scheduler activation without separate user approval.
