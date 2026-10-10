# G2 — Post-maturity outcome readiness gate (10.10.2026)

**Owner:** ChatGPT forecasting/prospective line. Independent from Codex-colleague TREND/BALANCE. Stacked on PR #146; no changes to those research files.

## Operational problem

The first six uncalibrated ForecastRecords were frozen on **2026-10-10 15:15:48 UTC** from 30 original MOEX response pages. All include immutable 5/10/20-session directions and source SHA. Their actual future horizons **cannot be completed today**. Calling reconstructed backfills "live outcomes" would violate the time/knowledge contract.

This contribution adds a **read-only, fail-closed maturity inventory** in `scripts/g2_post_maturity_outcome_readiness.py`, with separate negative tests. It checks:

- Exactly six frozen markets (SBER/Si/BR/GOLD/IMOEX/RTSI), exact SECID, snapshot_id, forecast_id and UTC T0.
- All original 30 raw provider pages against stored SHA256; source manifest hash matches frozen manifest.
- Every ForecastRecord canonical SHA against manifest/receipt; source receipt/last completed bar never later than decision T0.
- Each of the 18 (6×3) forecast/horizon pairs is independently marked pending until enough **later exact-contract** D1 evidence is available.
- Even when 5/10/20 later closes are available, **no canonical outcome is appended without an independently verified, complete exchange-session calendar**, proper provider receipt and source provenance. Merely counting later trading-looking D1 candles is insufficient; gaps/rollovers/late revisions cannot silently compress time.
- No outcome mutation, no retrospectively altered forecast, no model-quality claim. The code doesn't connect to DuckDB, HOME or network; it's safe for a downloaded evidence ZIP.
- An optional future-source ZIP is inspected read-only, but absence of trusted exact-SECID completed calendar evidence returns PENDING. The future ZIP never serves as "independent" calendar attestation by itself.

## Run and evidence

```bash
python -m scripts.g2_post_maturity_outcome_readiness \
  --frozen-zip path/to/g2_first_six_real_forecasts_20261010.zip \
  --output /scratch/g2_post_maturity_readiness.json
pytest -q tests/test_g2_post_maturity_outcome_readiness.py
```

Output is deterministic relative to the unchanged input ZIP and contains every forecast ID and individual horizon, status and evidence SHA. Six forecasts × three horizons = **18 PENDING** before any future trading observations. The same archive's SHA must be recalculated when moved, and report data must not be considered external time attestation.

## What cannot be finalized on 10 October

1. Actually completed future trading sessions for +5, +10 and +20 **do not exist yet** for 10 October forecasts.
2. There is no demonstrated independent, provider-authenticated exchange session calendar connected to the future source and correct contract at each maturity.
3. True prospective predictive quality requires future realized observations, statistical coverage, non-overlapping evidence and comparison to frozen baselines. This gate never claims accuracy.
4. Existing Stage-E **first-touch** objective outcomes are separate from the simplified horizon-end *direction* used in the initial staging pilot. The reader must not substitute endpoint-direction accuracy for UP_FIRST/DOWN_FIRST/NEITHER/AMBIGUOUS or fabricate intrabar MFE/MAE.

**Next engineering slice:** implement an independently authenticated calendar reader and an exact-SECID post-capture D1/OHLC collector via the existing MOEX governor. Require truly completed horizon, stored original provider bytes, stable revision policy and separate timestamp before using `CanonicalProspectiveStagingBridge.observe()` in a disposable staging copy of the journals. Independently review the result before any production activation.

All upstream PRs #132–#146 remain unmerged Draft unless separately approved; this PR makes no merge, deploy, HOME restart, trading action, secret access or 2023–2024 holdout access.
