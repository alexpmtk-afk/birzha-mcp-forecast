# SBER historical H1 / strict M15 research archive (2025–2026)

**State:** isolated implementation under review; cannot claim full backfill or
independent session/calendar verification without a completed measured report.
This work is stacked on the separate G2 research archive at
`ccaa52ecdab48f0c96a0d582b3b9518fd045dd82`, not `main`.
No trade execution; Forecast V1 unchanged; D1 history untouched.

## Contract and explicit limits

- Exact native MOEX SBER/TQBR H1 plus M1 -> strict derived M15.
  Raw M1 rows are in memory only and **not stored**.
- The existing historical D1 archive remains the D1 source. Neither source
  D1 nor production HOME DuckDB is migrated, rewritten, or imported here.
- Exact SBER, SECID=SBER, engine=stock, market=shares, board=TQBR.
- Each of 15 different one-minute completed candles must be available for M15.
  Price silence or missing provider minutes **cannot** be silently filled.
  Each rejected M15 bucket reports its timestamp, missing M1 starts and duplicate
  count; rejected H1 reports begin/end and reason.
- H1 is native MOEX H1; any shortened interval is rejected, **not**
  silently extended into a nominal 60-minute bar.
- The permanent key in the dedicated DuckDB is (SECID, TF, begin).
  Equal snapshots reuse rows. Conflicting OHLCV/source are rejected.
- A calendar civil date is **not** necessarily the exchange trading date.
  Weekend session may be assigned to the following exchange trading day.
  No trading holiday is inferred merely from Saturday/Sunday or empty candles.
  Until verified, the journal says CALENDAR_UNVERIFIED or EMPTY_SOURCE.
- Historic first receipt/point-in-time provenance is unproven. This is a
  research archive only and does not certify identical Forecast V1 replay:
  live Forecast V1 can aggregate partial minute groups into M15.

## Operational gates

All invocations are *offline plan only* unless a mode is named. Even source
PROBE refuses today's Moscow date and future dates; the upper bound for
backfill is the conservative published-history cutoff from
`latest_safe_d1_calendar_date()` (at 2026-10-13 evening, 2026-10-12).

Only the existing `research/SBER/` directory is accepted for a named,
separate `birzha_intraday_research_2025_2026.duckdb` file.
A pre-existing file without the exact research marker is inspected read-only
and rejected before writing. Do not put the DB in a public Git commit.

Commands (from repository root):

```bash
# Offline plan; all calendar dates included, including weekend sessions.
python -m scripts.g2_sber_history_batch --from-date 2025-01-01 --through-date 2026-10-12

# Read-only bounded SBER source samples; up to 5 dates per invocation.
python -m scripts.g2_sber_history_batch --mode probe --from-date 2026-10-09 --through-date 2026-10-09 --max-days 1

# For real HOME workstation ONLY, after the source pilot and legal right to use
# historical prices have been independently accepted:
mkdir -p research/SBER
python -m scripts.g2_sber_history_batch --mode ingest --from-date 2025-01-01 --through-date 2026-10-12 --max-days 3 --chunk-days 2 --archive research/SBER/birzha_intraday_research_2025_2026.duckdb --ack-research-write --ack-source-verified

# Report on every civil day in the range; no writes. Includes unresolved dates
# and per-interval audit evidence for dates actually fetched:
python -m scripts.g2_sber_history_batch --mode report --from-date 2025-01-01 --through-date 2026-10-12 --archive research/SBER/birzha_intraday_research_2025_2026.duckdb
```

Rerun the same bounded `--mode ingest` command to continue past captured
dates. Source-empty dates and dates with missing H1/M15 are *unresolved*
and skipped unless `--retry-missing` is explicitly supplied.
Incomplete candle dates are similarly skipped unless `--retry-partial`
is supplied. Errors are recorded with a reason, and can be retried.
Normal repeats do not redownload H1/M1 for already completed days.

After a **positively reviewed** real source and storage pilot, bulk may use
`--ack-reviewed-pilot --max-days 100` with `--chunk-days 2`;
each completed date remains checkpointed and an interrupted process can
continue. This flag does not waive source terms, production/data restrictions
or independent calendar acceptance. Do not run the full collection until the
pilot and rights checks are accepted. The user-approved overall range is
limited to the latest *fully completed* published 2026 session.

## Evidence, calendar and public-data terms

- Actual source pilot 2025-06-09 in prior Draft #234:
  SBER native H1=16 accepted, raw M1=992 transient,
  M15=64 strict complete, 5 incomplete M15 buckets, 2 rejected H1.
  This is PARTIAL source quality, not full session verification.
- Official MOEX 2025 holiday schedule:
  https://www.moex.com/n73702
- Official 2026 trading changes and added holiday weekend sessions:
  https://www.moex.com/n96571?nt=113
- MOEX 2026 weekend schedule and sessions belonging to the following
  trading day: https://www.moex.com/n95564?nt=111
- MOEX market-data usage terms:
  https://www.moex.com/en/datapolicy/
  and https://www.moex.com/a7156

MOEX data rights for historical bulk capture and private repeated research
must be reviewed before any complete two-year collection. Accessible public
ISS candles alone do not prove a redistribution/storage licence.

## Real verification strategy

A new branch-only GitHub Actions workflow tests the complete repository
offline, probes five SBER dates in both years (including a 2026 weekend),
then performs an **ephemeral one-day DuckDB pilot** after successful probes.
The source candle data are not uploaded as public GitHub artifacts and do not
persist between ephemeral hosted jobs. Only counts, quality and error
statistics appear in CI logs.

This tests the process, but does **not** populate the user's HOME DuckDB.
Only a real HOME/runtime write to the isolated research/SBER archive can make
the permanent saved-history criterion PASS.

## Coverage acceptance

From the designated independent research file, report at minimum:
- SBER days / source-complete, gaps, missing-inputs, empty-source, errors,
  unvisited, exact H1 and M15 row counts, duplicate keys and file bytes;
- exact missing minute starts and shortened H1 bars for every fetched date;
- all candidate **civil** dates, then independent reconciliation to the
  official instrument/session calendar, including weekends/holidays;
- retries/reused records and rejected conflicts;
- evidence of source terms, separate D1 availability, and PIT limitations.

**No claim of complete two-year coverage** is permitted until every expected
exchange session has a verified completed collection or a flagged issue.
The present software provides civil-date inventory but does *not yet*
independently certify the exchange trade-date mapping; this remains a blocker.


## Actual completed CI / hosted pilot, 2026-10-13

Branch workflow: https://github.com/alexpmtk-afk/birzha-mcp-forecast/actions/runs/38440053470
Tested implementation SHA: `67c266a2f85725d1671dcb89175bb130cc9afa58`.
Workflow **COMPLETED SUCCESS**: 30 targeted tests PASS, 1314 full offline
tests PASS, five live MOEX source/date jobs PASS as connectivity/source probes,
and disposable single-day DuckDB pilot PASS.

| Moscow civil date | Native accepted H1 | Source M1 transient | Strict M15 stored/eligible | Incomplete M15 buckets | Rejected H1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2025-06-09 | 16 | 992 | 64 | 5 | 2 |
| 2025-12-15 | 17 | 996 | 65 | 4 | 1 |
| 2026-06-15 | 17 | 1009 | 66 | 3 | 1 |
| 2026-07-18 (Saturday) | 10 | 540 | 35 | 2 | 0 |
| 2026-10-09 | 18 | 1009 | 66 | 3 | 0 |

All five samples are **PARTIAL_NOT_FULLY_VERIFIED** under strict completeness,
not absence of MOEX data. The Saturday evidence proves that a weekday-only
filter would silently omit valid prices.

The **ephemeral** test archive for only 2025-06-09 stored
H1=16 and M15=64, M1=0, duplicate keys=0, file size=2,371,584 bytes
(DuckDB includes fixed overhead). The source/day journal status was
CAPTURED_WITH_GAPS, and a repeat command skipped the date with no second
H1/M1 fetch and without an extra journal attempt. This temporary file was
not uploaded/persisted to HOME.

The actual stored gap audit for 2025-06-09 identifies five M15 buckets
starting **06:45, 18:30, 18:45, 19:00 and 23:45 Moscow**. Their incomplete
minute sets correspond in time to possible opening, main-session close,
evening opening or final auction boundaries. This is a *hypothesis* based
on the hours, **not independent certification that missing minutes were
non-trading**. Rejected H1 source intervals were
18:00:00–18:51:10 and 23:00:00–23:59:56 Moscow, both preserved in
the audit with their actual begin/end. No short or incomplete interval
was converted into a false complete bar.

As at the conservative cutoff 2026-10-12, the range beginning
2025-01-01 consists of **650 civil dates**, not 650 certified MOEX
trading sessions. Bulk planning has at least two logical candle-family
fetches (H1 and M1) per date that requires collection, plus instrument
resolution and possible paginated calls. Full HTTP call counts, latency,
memory use and final disk bytes are **not** extrapolated as proved from
a one-day file.

### Final remaining blockers

- No user HOME research/SBER DuckDB has been written: **permanent coverage
  zero confirmed**, all other days not persistently captured here.
- Formal per-session calendar and weekend-to-next-trading-day reconciliation
  remains missing; observed timestamps alone cannot prove completeness.
- MOEX historical bulk-storage / usage rights remain to be checked for
  the intended use; public ISS access does not settle this.
- Forecast V1 partial-group M15 behavior differs from strict research
  sampling, so identical V1 replay is not yet certified.
- Extrapolated two-year archive completeness is **NOT PASS**. Stop before
  a full 650-civil-date transfer without the above acceptance gates.


## Official MOEX 2025/2026 stock-market civil-date schedule (added)

The independent market-schedule **policy** is encoded in
`src/birzha/application/sber_stock_market_calendar.py` using only
published MOEX notices for 2025 and 2026, not a general-purpose Russian
workweek. Every date is assigned provenance and one market-level class:
open regular weekday, open named holiday, open regular working Saturday,
open additional-weekend session, or closed for a specifically published
market holiday/weekend exception.

Original notices and applicability:
- 2025 holiday/working-Saturday bulletin (2024-10-02):
  https://www.moex.com/n73701 ;
  2025 official weekend-exception schedule starting 2025-03-01:
  https://www.moex.com/n77967
- 2026 holiday/open-holiday bulletin (2025-10-06):
  https://www.moex.com/n94172 ;
  2026 official weekend-exception schedule (2025-11-25):
  https://www.moex.com/n95564
- MOEX says weekend sessions are integrated into the NEXT trading day
  https://www.moex.com/n91637 ;
  the current permitted weekend-session indicator on the SBER/TQBR
  instrument page is separate from an audit of historic daily entitlement:
  https://www.moex.com/ru/issue.aspx?board=tqbr&code=sber

Representative official-calendar cases:
- 2025-03-01 and 2025-06-07: additional Saturday sessions scheduled;
  2025-03-22: explicitly closed; 2025-06-12: exchange closed holiday;
  2025-11-01: ordinary working Saturday; 2025-11-03: open holiday.
- 2026-01-03: closed; 2026-01-05: open official holiday;
  2026-06-20: weekend exception/no session; 2026-07-18: scheduled
  weekend session corroborated by real H1/M1 probe; 2026-10-24: closed.

**Important limitation:** these are exchange STOCK MARKET schedule rules,
not a certification of individual historic SBER/TQBR availability or
historical amendments. Both the trading-date assignment of Saturday and
Sunday candles and precise intraday auction windows remain separate
contract checks. Published closed dates are not incorrectly filed as
missing candles; evidence of candles on such a date produces
`CALENDAR_SOURCE_CONFLICT` instead of being hidden. On published market
open dates the absence of candles remains `UNVISITED` or
`MISSING_INPUTS` until verified.

The market schedule can now be audited in fully offline `--mode plan`
output, and `--mode report` distinguishes scheduled civil dates from
exchange-wide announced closures, with source URLs and reasons for every
unresolved scheduled date. The live backfill skips announced closure dates
without querying MOEX. **This reduces unnecessary requests, but does not
authorize full backfill.** Official paid machine-readable trading-calendar
API is a separate MOEX information product:
https://www.moex.com/n89023

Massive repeat historical capture still requires checking whether the
intended automated archive/reuse is covered by user's market-data agreement:
https://www.moex.com/ru/datapolicy/
and https://www.moex.com/ru/products/personal


### Official-calendar-only offline inventory through 2026-10-12

For the period `2025-01-01..2026-10-12`, the checked-in MOEX
2025/2026 published-notice rules classify **650 calendar dates** as:

| Exchange-wide published schedule | Civil dates |
| --- | ---: |
| Regular stock weekdays | 437 |
| Additional weekend sessions | 130 |
| Open stock holiday sessions | 13 |
| Regular working Saturday, 2025-11-01 | 1 |
| Closed named exchange holidays | 20 |
| Closed weekends before weekend sessions began in 2025 | 15 |
| Explicitly excluded weekend sessions | 34 |
| **Total scheduled market-open civil dates** | **581** |
| **Total exchange-wide closed civil dates** | **69** |

These figures count **civil dates**, *not* unique exchange trading dates
or verified SBER sessions. The weekend sessions may be rolled into the
next trade-date identifier and SBER instrument eligibility is not proven
per historic day. The count is pinned in
`tests/test_sber_stock_market_calendar.py`; changing calendar bulletins
requires an explicit reason and a test update. The 581 market-open
civil dates remain unfilled in permanent HOME storage here.


### 2026-10-13 REAL full-source archive and official amended calendar (post-backfill)

Private, isolated GitHub runner completed 2025 and all conservative
historically completed 2026 dates through **2026-10-12** in GitHub
Actions **38446021926**; no HOME/production/D1 writes or merges.
Final unmodified SBER DuckDB: **12,333,056 bytes**, **8,693** valid
native H1, **33,486** strictly complete derived M15, **zero**
duplicate keys. M1 was only used transiently, never persisted.
GitHub Actions ZIP artifact: `SBER-DuckDB-2025-to-2026-10-12-PRIVATE`.
Its initial archive was also copied to a restricted Google Drive folder.

**Root cause of four apparent empty weekends found after actual
download:** the opening schedule announcement was *superseded* by
explicit MOEX bulletins, so these civil dates were **not missing data**:

| Civil dates (Moscow) | Definitive MOEX bulletin | Meaning |
| --- | --- | --- |
| 2025-08-09, 2025-08-10 | https://www.moex.com/n92346 (28 July 2025) | exchange ran tests; real weekend sessions CANCELLED |
| 2025-11-22, 2025-11-23 | https://www.moex.com/n95363 (17 November 2025) | production market technical maintenance; weekend sessions CANCELLED |

The policy now records those closures, with *exact bulletin provenance*.
The original row in `research_sber_batch_dates` remains intact as
`EMPTY_SOURCE_CALENDAR_UNVERIFIED` (for audit and replay). A report
against the corrected calendar labels each `MARKET_CLOSED_RECONCILED_EMPTY_SOURCE`,
not a missing candle and NOT `CALENDAR_SOURCE_CONFLICT`, provided
**no candles and no capture checkpoints** exist. Any contrary market
data or an error/other existing journal status is explicitly flagged
`CALENDAR_SOURCE_CONFLICT`, never silently accepted.

2025-01-01..2026-10-12 revised published **civil date** partitions:
650 total = **577 scheduled market-open** + **73 market-closed**,
including **126 additional weekend-session dates**. The source archive
had valid candles on all 577 scheduled civil dates; strictly incomplete
session-edge M15 buckets and truncated published H1 intervals remain
independently audited, therefore whole-session data integrity / exact
trading date / PIT certification are **not claimed**. This is source
coverage, not proof that every expected candle exists.

A further official bulletin https://www.moex.com/n104837
(5 October 2026) also changed the *future* November 2026 schedule:
04 November was announced for a weekend-format trading session, and
28-29 November were cancelled. This rule is encoded, but data for
future dates are **not** captured or implied to exist.

**Remains out of scope until an independent session spec exists:**
certify the exact open/close/auction gaps and the weekends' following
MOEX trade-date mapping. Do not assert these unresolved source-boundary
gaps are automatically genuine missing liquid trading minutes.
