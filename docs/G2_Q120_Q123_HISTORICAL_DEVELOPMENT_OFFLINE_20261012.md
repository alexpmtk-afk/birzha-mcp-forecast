# Q-120–Q-123 — restore 2021–2022 historical research priority

**User intent:** old historical dates first. No need to issue a new live
Forecast merely to exercise a backtest or improve old-date computation.

Base: Draft #224 exact SHA ed4e9905c8e29798018b06b07f251fa06ebaf3af.
New script: `scripts/g2_d1_historical_diagnostic.py` used only on
a **local disposable COPY** of a user-supplied 2021–2022 archive,
paired with that exact archive's V2 warmup evidence JSON.

## Q-120: old dates and three historical D1 horizons

The existing six-market D1 Development extractor already reconstructs
research-only features as of each historical active-root trading
session in 2021–2022. This adds a read-only retrospective diagnostic
that, for each extracted row, evaluates the NEXT 5/10/20 recorded
sessions, using the frozen SECID and the historical closing price
at that session as reference. The baseline directional rule is a
**predeclared D20 ATR sign**. It is a research plumbing comparator,
**not** the existing Forecast V1; its prediction accuracy MUST NOT
be credited to Forecast V1 or presented as protocol completion.

## Q-121: no rollover leakage or gaps

The code looks up the exact active root's sequence of dates after T0.
Every row in a horizon must stay on the same original SECID, must be
completed, unique by original D1 session, and have finite valid OHLC.
Any futures roll inside a horizon, incomplete session window,
unexpected missing/duplicate raw candle or date past Development
is excluded with an explicit reason. It never stitches old and new
futures roots or pretends a missing session is a trading day.

## Q-122: temporal separation of input features and future outcomes

Only source features extracted from the D1 candle window ending at
the T0 historical session are used to choose the baseline direction.
The following 5/10/20 session closes are read only during evaluation,
not fed into T0 features. Negative tests replace a future price and
verify that the earlier feature payload is byte-equivalent while
the appropriate forward outcome changes. No external network calls
or request to rerun contemporary LIVE market data.

## Q-123: reproducibility and protected holdout isolation

The script refuses any decision or future session outside the
declared exposed 2021-01-01..2022-12-31 DEVELOPMENT interval.
Neither held-out 2023–24 nor post-Development future rows may be
used. It demands `historical_first_receipt=NOT_PROVEN`,
`decision_knowledge_cutoff_at=null` and
`RECONSTRUCTED_RESEARCH_ONLY`; fabricated historical source
receipts or disguised LIVE evidence are rejected.

The archive is read with DuckDB read_only=True and the exact archive
SHA-256 is verified before and after. Report JSONL files and a
deterministic manifest record exclusions, code/source/Warmup hashes,
and explicit `Forecast_V1_measured=false`, `release_allowed=false`.
Different existing output files are never overwritten.

## Important evidence boundaries

This is **retrospective reconstructed research**, not a true PIT
backtest: original historical network first-receipt timestamps and
the version of the MOEX calendar known at historical T0 have NOT
been established. The 2021–2022 original data may have revisions
learned after the target date; this cannot be repaired by minting
a new timestamp today. Forecast V1 is not being directly measured
in this task, and no conclusion on forecast predictive significance,
real-money trading or 2023–24 untouched OOS is warranted.

The actual large historical DuckDB archive was NOT found as a Drive
attachment or in the GitHub source repository during this task,
so execution against the true retained user copy requires the
verified mounted archive + matching original warmup V2 evidence.
GitHub CI runs synthetic 2021 source fixtures, NOT real user archive.
Do not fabricate empirical market-performance counts.

All 12 canonical release blockers remain open; no LIVE issuer,
main, HOME, production, trading, holdout unlock or Forecast V1
scoring change is authorized.

## Acceptance and next deliverable

Require SHA-exact full CI, negative no-lookahead and SECID-roll
tests, compilation, repo Security/Preflight PASS and no release.
Next obtain a read-only COPY of the real 2021–22 archived DuckDB,
verify its SHA and V2 JSON, execute this exact immutable script,
then independently inspect true counts and exclusions. After that,
consider original Forecast V1 retrospective predictions in a
separate clearly labeled non-PIT experiment.
