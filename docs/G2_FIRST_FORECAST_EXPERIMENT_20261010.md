# G2: First Forecast Development Experiment — 10 October 2026

## Ownership and scope
Owner: ChatGPT engineering line. This independent zero-fit forecasting baseline does not alter the Codex-colleague's G2-TREND-BALANCE-RESEARCH files. The PR is based on PR #137 commit 36ac2f9a6c7d28838e3e863b613a574be37d8fa6; there are no changes to Forecast Engine, HOME, archived DuckDB, external trading or 2023–2024 holdout.

Source: six-market Development D1 research-only JSONL (2021–2022), SHA256 a5f16444d5b65ed26090a13ca01224eddd4c7d6e8a2488d4d71a7a3f8b186aef, 2965 rows, from https://drive.google.com/drive/folders/1VcAHfK6r5dW3HXzWlYH3v8stOPTw58bH . Feature version G2_D1_RESEARCH_SMA_TR14_D20_ER20_W20_V1. No proven historical first-receipt/vintage and no strict as-known-at-T0 capability.

Experiment version G2_FIRST_FORECAST_D5_SIGN_ZERO_FIT_V1. Horizons 5, 10 and 20 exact trading sessions. No fitted parameters, no outcome-driven threshold selection, no probability calibration.

## Frozen candidate and comparator
At the originating D1 close, predict the sign of d5_atr computed **at origin only**: positive=UP, negative=DOWN; zero/missing/nonfinite=ABSTAIN. Issue the same fixed signal for all horizons. Comparator always predicts UP on *exactly the same scored rows*. Accuracy = fraction of correct directions among evaluated issued predictions; flat realized movement is wrong for directional predictions.

Prediction rows are fully written to a separate file before generating any outcomes. These predictions are reconstructed Development diagnostics, never immutable real-time or ex-ante claims. The method forecasts direction, not price levels.

## Outcome from future feature rows only
The accepted D1 feature engine defines dN_atr = (close at t − close at t−N) / ATR14 at t. Therefore the future price difference can be reconstructed from future feature records without separately reading DuckDB:
- 5-session outcome at t+5: future d5_atr × future ATR14.
- 10-session outcome at t+10: 5-session delta at t+5 plus 5-session delta at t+10.
- 20-session outcome at t+20: future d20_atr × future ATR14.
Future features are used **only for outcome evaluation, never to set predictions**. The code verifies exact archived 21-session calendar relations, same exact SECID and (for +10) the intermediate record. No cross-contract stitching, assumed calendar weekdays, gap compression, or fabricated price levels. Missing target/midpoint remains UNSCORED.

## Reproduction
From repository root with Python >=3.12; standard library only:

    python -m scripts.g2_first_forecast_development --dataset /path/g2_d1_development_rows.jsonl --output-dir /path/g2-first-forecast-development
    pytest -q tests/test_g2_first_forecast_development.py

Outputs:
- g2_first_forecast_predictions.jsonl — predicted directions by market/SECID/origin/horizon, 8895 rows, SHA256 1aa7bb7f1aefa12fb21731b24ac368cfc3821187353469eb7bd1f070d675d72b
- g2_first_forecast_outcomes.jsonl — future outcomes, excluded/unscored reasons, SHA256 31c379fdd07ef6d30d330f184756e399ac8a43f5d88fe592d5f1976a4a55be1c
- g2_first_forecast_summary.json — market/horizon metrics and manifest, SHA256 2a3a5ba44dc43ea4ec8b881d8d4cf656fadaa39cfa2d725fbd4a0e6ae9625338

## Measured reconstructed Development result
Independent ChatGPT Python execution, 10 local tests passed.

| Horizon | Candidates | Scored | D5-sign correct | Always-UP correct on same rows | D5 accuracy | UP accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 | 2965 | 2733 | 1425 | 1392 | 52.14% | 50.93% |
| 10 | 2965 | 2503 | 1291 | 1270 | 51.58% | 50.74% |
| 20 | 2965 | 2064 | 1051 | 1061 | 50.92% | 51.41% |

An independent algebraic check for +20 outcomes used 20×(SMA20 at t+20 − SMA20 at t+19) versus future d20_atr×future ATR14, matching to floating-point precision on all checkable exact-SECID pairs.

Do NOT claim quality improvement. The apparent differences are small, sample-specific, and outcomes/horizons overlap heavily; IID confidence intervals are invalid. The crude D5 strategy loses to always-UP at 20 sessions. Cross-contract exclusion dramatically reduces some market coverage: BR +20 scores just 30 of 506 candidates (5.93%). Coverage is a validity gate, not a model skill measure.

## Acceptance
PASS = bounded Development experiment completed with reproducible predictions, outcomes, hashes, negative tests and per-market coverage. NOT PASS = strict PIT/historical as-known-at-T0, out-of-sample forecasting, economic profitability, production deployment, or final model selection.

Next: ChatGPT maintains own forecasting/outcome line and designs immutable prospective test and better baseline controls; Codex-colleague independently implements TREND/BALANCE/UNKNOWN. Combine candidate ideas only after review. Preserve 2023–2024 for a future explicitly approved independent gate. No merge or deploy authorized.
