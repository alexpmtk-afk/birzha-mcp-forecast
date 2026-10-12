# Q-124–Q-128 — independent six-market historical Development review

Development dates: **2021–2022**. Date of engineering audit: **2026-10-13**.
Stacked after draft PR #225 `83d36ac9d965bbc3eee1aadbb3d6fee9266063b0`.
No user HOME access, actual original archival DuckDB, 2023–2024 holdout,
current-date Forecast, provider network, production DB or live trading.

## Q-124 — real historical derived dataset, not synthetic CI

Located on user's connected Google Drive:
`g2_d1_development_rows.jsonl` (5,937,846 bytes) and
`g2_d1_manifest.json` in original folder
`https://drive.google.com/drive/folders/1VcAHfK6r5dW3HXzWlYH3v8stOPTw58bH`.
Independent local read verifies SHA-256
`a5f16444d5b65ed26090a13ca01224eddd4c7d6e8a2488d4d71a7a3f8b186aef`,
exactly matching signed-*by-hash* research manifest, 2965 rows:
BR 506, GOLD 506, Si 506, SBER 489, RTSI 489, IMOEX 469.
This is internally checksum consistent, NOT third-party signed source or
historical first-receipt authentication. Its original DuckDB archive
SHA is a **claim in the manifest**, not independently reverified today.

`scripts/g2_d1_overlap_independent_audit.py` now requires exact
SHA-bound Development feature file + matching manifest, prohibits
authentic/vintage/holdout assertion upgrades and never reads LIVE MOEX.

## Q-125 — independent published experiment reproduction

Ran a separate Python historical-feature/label calculation on the
Drive derived dataset, matching previously published
`docs/G2_FIRST_FORECAST_EXPERIMENT_20261010.md`.
The predictor is D5-sign, a zero-fit research baseline, not Forecast V1.
Always-UP comparison uses exactly the same rows.

| Horizon sessions | Candidates | Scored | D5 hits | D5 accuracy | UP hits | UP accuracy |
|---|---:|---:|---:|---:|---:|---:|
| 5 | 2965 | 2733 | 1425 | 52.14% | 1392 | 50.93% |
| 10 | 2965 | 2503 | 1291 | 51.58% | 1270 | 50.74% |
| 20 | 2965 | 2064 | 1051 | 50.92% | 1061 | 51.41% |

No claim of statistically significant improvement; apparent differences
are small and label horizons overlap substantially.

## Q-126 — non-overlap and by-year stress checks

A fixed predeclared descriptive thinning algorithm processes scored
predictions in chronological order **per market and horizon**. It
retains an origin only if that origin is at/after the last retained
target session. It does not cherry-pick by correctness.

| Horizon | Nonoverlap n | D5 accuracy | Always-UP accuracy |
|---|---:|---:|---:|
| 5 | 566 | 53.36% | 51.24% |
| 10 | 277 | 57.40% | 51.26% |
| 20 | 132 | 47.73% | 53.03% |

The 10-day higher descriptive accuracy is a MUCH smaller,
potentially biased subsample and is **not** independent proof of
forecast edge. The 20-day diagnostic falls below Always-UP.
By-origin-year results diverge (2021 vs 2022); they are descriptive
within exposed Development and do not constitute independent OOS.

## Q-127 — first frozen predictions cannot be clobbered

Old first-forecast research code wrote outputs directly and would
truncate existing predictions/outcomes/summary on rerun. Changed
to immutable no-clobber, atomic temporary artifact linking, exact
identical repeats only and explicit symlink refusal. Original input
SHA verified before/after; results stay separate from real Forecast
journals. No model scoring formula changed.

## Q-128 — optimize-mode fail-closed validation

The first experiment used `assert` for untrusted feature source
acceptance, meaning `python -O` could remove critical checks. Replaced
with explicit ValueError identity, origin/21-session/date/provenance
checks, and duplicate-session rejection even under -O. The feature
reader rejects booleans as numbers; ambiguous future calendar maps
cannot silently overwrite keys. Negative subprocess -O tests included.

## Limitations and next real acceptance

All empirical figures above were computed from *derived historical
reconstruction*; the ORIGINAL 2021–22 DuckDB file and source vintage
were not loaded during this review. A SHA of a derived file cannot
prove which exchange records were available at historical T0.

The present dataset has D1 features only. Complete Forecast V1 also
needs causal H1/M15 inputs and other snapshot structure. Do not
substitute this D5-sign diagnostic for full Forecast V1 validation.

Protected 2023–24 remains untouched. The immutable production release
gate must stay blocked (12 outstanding technical/permission items),
regardless of this Development research progress. No change to
Forecast V1 parameters, main, HOME, production, releases or trading.
