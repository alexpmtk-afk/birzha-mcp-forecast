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
