# SBER research-only read adapter (non-production)

State: Draft implementation for tests and independent technical review only. No
automatic MCP registration, Forecast V1 feeds, HOME installation or Google
Sheets writes.

## Exact source / provenance

Based on stacked Draft PR #236 SHA f3f347dd1d75a6eddba64b07ce2e5ae9a36c1f73
and parent Draft PR #234. Private SBER-only research file:
birzha_intraday_research_2025_2026.duckdb from Google Drive archive
SBER_2025_2026_VERIFIED_RECONCILED_PRIVATE.zip.
Verified DuckDB SHA-256:
95a0b0527e287c4b32bd87e4109478e6d1f794d0654b472e6324bcba2f7dc990.
8,693 original strictly full H1, 33,486 strict M1-aggregated M15; 577
calendar dates contain bars. ALL captured days have quality WITH_GAPS.
It is not full native hourly history because 627 short native bars were
rejected; 2,066 M15 partial groups were not published as complete.

## Usage (after downloading to a DISPOSABLE or approved dedicated research path)

    from birzha.application.sber_research_read_only import SberResearchReadOnly
    with SberResearchReadOnly(
        '/isolated/private/research/SBER/birzha_intraday_research_2025_2026.duckdb',
        expected_sha256='95a0b0527e287c4b32bd87e4109478e6d1f794d0654b472e6324bcba2f7dc990',
    ) as reader:
        inventory = reader.inventory()
        sample = reader.read(timeframe='M15', from_civil_date='2025-06-09',
                             through_civil_date='2025-06-11', max_bars=1000)
        assert sample.research_only and not sample.forecast_eligible

Reader uses duckdb.connect(read_only=True) directly; it never instantiates
ResearchIntradayArchive (whose initializer is write-capable). A dedicated
source marker, pinned SHA, SBER stock/shares/TQBR identity, expected source
kind, daily capture checkpoints, duplicate keys, strict boundaries, OHLCV
and upper bound are validated. No silent truncation or source network calls.

Date field trade_date in the source is CIVIL DATE. Output field civil_date
is the candle's Moscow start date, with trade_session_date UNKNOWN,
session_id UNKNOWN, available_at UNKNOWN, observed_at UNKNOWN,
revision UNKNOWN, historical first receipt NOT VERIFIED.
The H1 fingerprint corresponds to original source string time notation;
a naive new digest of a normalized ISO timestamp must not be used to
reject all genuine H1.

Individual bars pass strict interval lengths, but contiguous market sessions
are NOT verified: adjacent same-date gaps are emitted as unclassified
gap candidates, not invented missing trades. No Forecast V1 bridge exists.
Strictly historical replay remains BLOCKED pending real source receipt
and versioned exchange-session calendar.

## Google Sheets and HOME integration plan (no export now)

Memo 09 designates user folder:
My Drive / Биржа / Архив рыночных данных / SBER - Сбербанк
and existing spreadsheet MOEX_HISTDATA_SBER_MCP_CANONICAL.

Drive/readback update 2026-10-14 (Memo 09 revision 1.1):
- Canonical spreadsheet **remains the same ID**:
  `1DQojm2mzeXC41mHX1oH8xMv0d43TSz7U7o-ciK9lgoQ`
  in `Биржа / Архив рыночных данных / SBER - Сбербанк`.
  No renamed or new SBER book is required.
- Previous 49 H1 + 192 M15 pilot rows were superseded by a **one-time,
  user-authorized research-only copy** of the complete captured range
  2025-01-03..2026-10-12: **8,693 H1 + 33,486 M15 (42,179 candles)**,
  in the existing `H1`/`M15` tabs (A:Z), not a second source of prices.
- Separate read-only verification of the present exported spreadsheet
  found unique deterministic keys (0 duplicates), valid SBER source and
  OHLCV/duration constraints, pinned archive SHA consistent for every
  record, and **577/577 daily count matches for both H1 and M15** against
  `sber_2025_2026_coverage_RECONCILED.json` from the archived source ZIP.
  This is **not** an independent SQL OHLCV comparison against every
  source DuckDB row, nor a session or PIT attestation.
- `SYNC_STATUS` already includes `H1_*` and `M15_*` research
  readback entries with `HOME_SYNC_NOT_INSTALLED`; the top-level
  `YDB / D1-only` descriptors still reflect the pre-integration exporter.
  `D1`, `SESSIONS`, `TRADESTATS` and `VERIFIED_RANGES` were not
  migrated by this copy. No HOME installation or automatic export exists.
- The private ZIP copy and the native Google Sheet coexist in the
  canonical SBER folder. Original research capture remains preserved.
- **Do not write another 42,179 records or treat the one-off copy as
  an automated HOME->Sheets synchronization generation.**
  The existing mirror code hardcodes YDB/D1-only descriptors;
  manually changing `SYNC_STATUS` does not change that exporter.

Future versioned one-way exporter should:
- Read from one accepted dedicated HOME SBER research archive (read_only)
  and keep original working D1 in its current verified owner.
- Compare existing per-candle deterministic keys and OHLCV/source hash;
  same key different values must fail with an explicit conflict.
- Fill known civil date, exact ISO begin/end, SECID, source identity and
  quality; leave historical availability and exchange trade date unknown.
- Emit batches, re-read each generation for key/hash parity, then mark
  per-timeframe SUCCESS in SYNC_STATUS. Never overwrite old D1 PASS status.
- Test retries, conflicts, restart and recovery on copies first.
- Never write historical SBER OHLCV to any other instrument book.

HOME actual backend, directory permissions, backup status and verified
deployment permissions are not available to this GitHub-only authoring
session. No HOME deployment was performed. MOEX market-data reuse rights,
627 native H1 gaps, 96 atypical M15 gaps, exact weekend trade dates and
Forecast V1 aggregation contract remain separate acceptance gates.
