# Continuous futures: canonical *proposed* contract, v0.1-research (29.10.2026)

> **NOT INSTALLED. NOT FORECAST-ADMITTED.**
> This is a research proposal in a DRAFT pull request. It must not silently override
> installed `history.session_rules`, accepted RAW OHLCV, or existing Market/Forecast contracts.
> Approval requires the gates below and separate authorization for merge/HOME changes.

## Scope and precise terminology

1. **Session concatenation:** read exact `SECID + timeframe` chronological RAW observations across real sessions, with gaps intact. Already covered by `history.stored_series` / `load_exact`. **Never** adjust RAW OHLCV.
2. **Contract rollover:** select one effective **contract SECID per exchange trade session** for a root future, using only information available before the selection decision. Store a versioned `roll_event`.
3. **Continuous analytics:** create a derived price coordinate on demand from the same validated switch ledger. Never store adjusted prices as real execution fills or replace source history.

**RI (RTS futures) and RTSI (cash RTS index) are not interchangeable.**
The runbook previously linked the RI research Sheet under a `RTSI` label. Do not treat that link as proof of index history or wire RI through an RTSI resolver.

## Contract / data inputs (required before generation)

- Exact contract SECID, underlying root, first/last trading time, expiry, tick/point units.
- Official exchange `trade_session_date` for each source candle (not calendar begin date).
- Source immutable OHLCV + interval + completeness + revision + provenance / source hash.
- D1 volume for all eligible listed overlapping contracts on every possible decision session, and access to same-session close for both sides of any switch.
- A timestamp for each datum's **observed_at / available_at** and confidence. Use the **existing** `Candle.available_at_confidence` enum: `EXACT`, `DOCUMENTED`, `INFERRED`, `UNKNOWN` (do not invent `VERIFIED`). Strict source-first-seen replay requires `EXACT` with an auditable timestamp; mere documentation or inferred timestamps do not qualify. Missing source-first-seen is **UNKNOWN** and must fail the strict gate.
- Same pre-approved decision ledger used for D1, H1, M15; never independently pick three contracts.

## Baseline selection (RESEARCH, NOT UNIVERSAL APPROVAL)

For target trade session T use the **latest prior completed and available trade session** T-1:
1. Enumerate real eligible exact contracts of the same root. Reject expired ones on T.
2. Choose the highest **positive D1 volume on T-1**, using a deterministic SECID tie-break. Use only information already available at T's decision cutoff.
3. If no eligible contract has verifiable T-1 observations, or chosen contract has no bars on T: **BLOCK** / unknown; do not fabricate a switch.
4. For old→new calculate **basis = close(new,T-1) − close(old,T-1)**, only if both closes are observed and eligible in the overlap.
5. Expiry is a **safety constraint**; don't require a universal two-day confirmation or an arbitrary pre-expiry forced roll. Test explicit expiry fallback with a controlled case. Record `decision_time`, `decision_session`, `effective_session`, `old/new_secids`, `basis`, `ratio`, sources/SHA and method version.
6. Prevent `A→B→A` due accidental wrong universe, ambiguous session mapping or missing overlap; do not suppress genuine data silently. Explicitly flag instability for audit.

**Existing repo conflict:** `src/birzha/providers/moex_history.py::_pick_instrument` ranks the selected same-day MOEX history rows by `VALUE, OI, VOLUME`, whereas old Si research uses T-1 volume. This is **not one consistent causal selection algorithm**. No production resolver replacement is authorized by this draft. Implement and test an explicit shared T-1 selector after acceptance.

## Derived price coordinates

For switch j let `b_j = close(new,j decision) − close(old,j decision)` and `r_j = close(new,j decision) / close(old,j decision)`.

For selected RAW OHLC `P_t` and the switches known at analysis cutoff `T0`:

- **FORWARD_SPREAD:** `P_t - Σ_{j: effective_j ≤ t} b_j`. Past prices stay in original anchor coordinates.
- **ADDITIVE_BACK:** `P_t + Σ_{j: t < effective_j ≤ T0} b_j`. Latest contract is on its real current price axis.
- **RATIO_BACK:** `P_t × Π_{j: t < effective_j ≤ T0} r_j`. Intended for ratios/returns, never silently use as executable absolute price.

For a fixed same switch set, additive-back and forward-spread differ by **one constant across all OHLC**. Hence they have the same absolute point movements / true-range / Wilder ATR in point units. They have **different absolute price axes**, so model-level absolute thresholds and real price levels need explicit coordinate transformations. Ratio changes point amplitudes, preserves relative scaling by construction. Dynamic/as-of recomputation of Back requires versioned switch ledger; do not conflate changed historical plot coordinates with use of information unavailable at a historical prediction cutoff.

The caller **must declare** the purpose/coordinate and `T0`; refuse ambiguous requests. Default model activation is **DISABLED** until its actual input contract (Model №3) is audited.

## Contract-bound volumes, profiles and order flow

- Historical RAW volume, trade count, OI, Delta remain tied to exact SECID. Never add two contracts' observed volumes and pretend one contract traded that volume.
- Session VWAP / POC / VAH / VAL / HVN / LVN must cite exact contract + session + price axis. Research-based transfer of old levels requires explicit transformation & flag; do not market transferred levels as executable RAW levels.
- **M1 OHLCV cannot reconstruct exact traded volume at each price or true aggressor Delta.** Use appropriately qualified trades / order-flow provider for exact volume-by-price or Delta; otherwise mark `UNAVAILABLE`.
- M15 source density is **not** the same as absence of source trades. Store `observed_m1` (1–15) and `quality` separately. `SPARSE_M1_H1_MATCHED` means OHLCV aggregated from actual recorded M1 and independently reconciled with native H1; it does **not** prove omitted clock minutes contain no trades. Do not fabricate transactions, forward-fill RAW or silently pass a sparse interval into a strict-only model interface.
- Never discard a source-valid M1 observation only because its 15-min bucket has fewer than 15 minute candles; preserve in the RAW archive and a separately labelled research M15 view. An empty time bucket receives no synthetic candle.
- Native H1 with non-nominal source end must remain RAW, independently of `is_complete`. **Across RI Jan–Apr 402/1,772 native H1 have non-nominal source duration and 163,192 native volume, but all 402 exactly reconcile to observed M1; 306 are followed immediately by the next hour.** The original research collector assigned `is_complete=False` solely from `end-begin`. This mixes observed source span, interval finality, and model admission; these MUST become separate explicitly named quality fields. Do not silently flip accepted flags in canonical storage.

## 2026 RI evidence and status

- Source: official MOEX ISS FORTS RFUD, 4 exact SECIDs (RIH6, RIM6, RIU6, RIZ6), 512 D1.
- Three transitions: 20 Mar (+2570), 19 Jun (−2830), 18 Sep (−710) points of basis.
- Isolated workflow: https://github.com/alexpmtk-afk/msp-server-bridge/actions/runs/40930348533 (D1).
- Intraday source: https://github.com/alexpmtk-afk/msp-server-bridge/actions/runs/40932330635 (6 contract-pair windows; 854 H1, 37,168 M1, 1,456 strict M15).
- Strict M15 retained 21,840/37,168 source minutes (**58.76%**) and **87.58% of observed M1 volume**. Excluded M1 records ≠ proven missing trades. Independently recomputed 1,456 M15 OHLCV with no differences. There are 1,774 non-strict M15 buckets.
- Official MOEX 2026 weekend/holiday schedules: https://www.moex.com/n95564 and https://www.moex.com/n96571 . Weekend calendars in the three sampled windows map to the **next trade session**, including June 12 holiday. Sampled D1/H1/M15 effective contract identity aligned **in scope**, not general archive proof.
- Independently rederived Wilder ATR14 on Additive vs Forward: maximal absolute difference **0** on the 188 D1 selected observations. At 3 rollover boundaries both adjusted methods match new contract's same-contract point change; ratio matches its percentage change.
- Whole Jan–Apr exact-contract archive: **1,772/1,772 native H1 OHLCV match M1 exactly**. M1=78,666 and observed M1 volume=4,678,402. Existing strict M15=3,408 with 4,104,068 volume; **3,316 additional nonempty sparse M15** contain 27,546 observed M1 and **574,334 volume (12.2763%)** absent from strict-only M15. Research representation preserves all observed M1 volume. These are contract-volume units, not monetary profit.
- Research reconstruction and quality test: https://github.com/alexpmtk-afk/msp-server-bridge/blob/chatgpt/ri-rollover-d1-research-20261029/research/ri_m15_sparse_reconcile.py ; results in https://github.com/alexpmtk-afk/msp-server-bridge/pull/734.
- Current research decision code now separates **decision_ledger()**, which cannot inspect target-day bars, from ex-post `schedule()` used only for outcome verification. Strict PIT also rejects inferred source availability and incomplete prior D1.
- Model №3 context (29.10.26): **closed H1** is the primary spread/volume/progress/retention evidence; **D1** is context; **M15** is optional refinement. A sparse-M15 recovery does NOT by itself establish forecasting benefit. The production Model №3 engine and 54-field compatibility remain unverified. Source: https://drive.google.com/file/d/1thGHst8JKqzjU9-FTMPHbuV2zl9nEN-c/view .
- **All source PIT/first_seen unavailable historically; strict replay attribution is NOT PASS.**

## Mandatory acceptance gates (future, not done)

- **G1 source:** reproduce SHA of every RAW archive, inspect OHLCV and source/contract coverage.
- **G2 calendar:** independently attest `trade_session_date` for all target sessions incl. weekends, holidays, nights, clearnings; do not classify by calendar date alone.
- **G3 causal:** only D1 completed + available before decision `T0`; negative tests for late publication, missing overlap, missing candidate, expired contract and unstable switch.
- **G4 mathematics:** numerical identities for OHLC/adjustment/ATR and return coordinates, no arbitrary `0.5 ATR` or 10% agreement thresholds; historical prefix for T0 reproduced using only prefix-known event ledger.
- **G5 intraday:** H1/M15 use same effective contract by exchange trade session; require source-native H1 = aggregated M1 OHLCV, exact M15 volume conservation, separate strict vs sparse quality labels, separate `source_span_short` from `closed_at_T0`, and official session mapping. Jan–Apr H1/M1 arithmetic now PASS in research; actual Model №3 admission remains gated.
- **G6 Model №3:** real, version-pinned signals and absolute-level assumptions compared against genuinely available PIT data; OOS results and criteria defined before comparison.
- **G7 release:** code review, tests, version/schema migration plan, permissions, explicit authorization to merge, install, change DuckDB/Sheet/skills; readback on actual installed HOME and independent rollback.

**Passing historical D1/H1/M15 arithmetic does not pass G3/G5/G6/G7.**
Do not treat this draft document as an installed operational rule.
