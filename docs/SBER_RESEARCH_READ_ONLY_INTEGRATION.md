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

Current live readback (after consultant audit): H1 49 valid *pilot* records
in A2:Z50, M15 192 pilot records in A2:Z193, 9–11 June 2025.
Do NOT treat those 241 records as a completed mirror generation.
D1, SESSIONS, TRADESTATS, VERIFIED_RANGES and old SYNC_STATUS remain
untouched. The existing mirror code uses a D1-only contract and hardcodes
YDB/ON_DEMAND. Manual SYNC_STATUS edits are not a migration.

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
