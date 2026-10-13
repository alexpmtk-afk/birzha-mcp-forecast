"""Offline, strictly read-only access to the isolated SBER H1/M15 research archive.

This is intentionally NOT a MarketDataService, production HistoryStore or a
Forecast V1 data source. Every price bar is retrospective/research-only:
exchange trade-date, historical first-receipt time and uninterrupted session
coverage are unverified. Never manufacture these fields from the candle end.

The immutable DuckDB has a different schema from the D1 historical store.
This module does not use ResearchIntradayArchive (its constructor opens for
writing), does not create tables, and does not fetch MOEX data.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from birzha.application.history_policy import require_historical_intraday_scope

_ARCHIVE_ID = "BIRZHA_RESEARCH_INTRADAY_2025_2026_V1"
_SOURCE = {"H1": "MOEX_NATIVE_H1", "M15": "MOEX_M1_AGGREGATED_M15"}
_ALLOWED_QUALITY = {"CAPTURED_WITH_GAPS", "CAPTURED_SOURCE_RESPONSE"}
_REQUIRED_TABLES = {
    "research_intraday_meta",
    "research_intraday_candles",
    "research_intraday_captured_days",
}
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RESEARCH_WARNING = (
    "RECONSTRUCTED_RESEARCH_ONLY; NOT_PIT; SESSION_COVERAGE_UNCERTIFIED; "
    "NOT_AUTHORIZED_FOR_FORECAST_V1"
)


@dataclass(frozen=True, slots=True)
class SberResearchBar:
    record_key: str
    timeframe: str
    civil_date: str
    begin: str
    end: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    value: float | None
    source_kind: str
    source_fingerprint: str
    capture_day_quality: str
    # Deliberately empty: never infer exchange trading date from a civil
    # weekend, or historical availability from bar end or captured_at.
    trade_session_date: None = None
    session_id: None = None
    available_at: None = None
    observed_at: None = None
    revision: None = None
    available_at_confidence: str = "UNKNOWN"
    completeness: str = "INDIVIDUAL_BAR_DURATION_VALID"
    forecast_eligible: bool = False
    quality_warning: str = _RESEARCH_WARNING
    fingerprint_contract: str = "SOURCE_INPUT_STRING_FORM_V1"


@dataclass(frozen=True, slots=True)
class SberResearchRead:
    archive_sha256: str
    timeframe: str
    from_civil_date: str
    through_civil_date: str
    bars: tuple[SberResearchBar, ...]
    capture_days: tuple[tuple[str, str], ...]
    gap_candidates: tuple[tuple[str, str, str], ...]
    research_only: bool = True
    session_calendar_verified: bool = False
    point_in_time_verified: bool = False
    forecast_eligible: bool = False


class SberResearchReadOnly:
    """A snapshot-scoped, SHA-pinned, no-network DuckDB reader.

    The instance must be explicitly closed (or used with a context manager).
    Its connection is opened with read_only=True, never a write-capable class.
    Every read checks a SHA of the file again to detect outside modification.
    No silent clamping and no implicit import to D1 or MCP.
    """

    def __init__(self, path: str | Path, *, expected_sha256: str):
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._path = Path(path)
        if not _DIGEST.fullmatch(expected_sha256):
            raise ValueError("64-character lowercase expected_sha256 required")
        self.archive_sha256 = expected_sha256
        if (self._path.suffix != ".duckdb"
                or self._path.is_symlink()
                or not self._path.is_file()):
            raise ValueError("existing, regular non-symlink research DuckDB required")
        self._verify_file_hash()
        self._conn = duckdb.connect(str(self._path), read_only=True)
        try:
            self._validate_archive()
            self._verify_file_hash()
        except BaseException:
            self.close()
            raise

    def _verify_file_hash(self) -> None:
        h = hashlib.sha256()
        with self._path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != self.archive_sha256:
            raise ValueError("research DuckDB SHA256 mismatch; refuse unversioned change")

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise ValueError("research reader is closed")
        return self._conn

    def _validate_archive(self) -> None:
        con = self.connection
        tables = {
            r[0] for r in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main'"
            ).fetchall()
        }
        if not _REQUIRED_TABLES <= tables:
            raise ValueError("missing mandatory research archive tables")
        archive_id = con.execute(
            "SELECT value FROM research_intraday_meta WHERE key='archive_id'"
        ).fetchone()
        if archive_id != (_ARCHIVE_ID,):
            raise ValueError("wrong research archive marker")
        mixed = con.execute(
            """SELECT count(*) FROM research_intraday_candles
               WHERE symbol!='SBER' OR secid!='SBER' OR board!='TQBR'
               OR engine!='stock' OR market!='shares'
               OR timeframe NOT IN ('H1','M15')
               OR (timeframe='H1' AND source_kind!='MOEX_NATIVE_H1')
               OR (timeframe='M15' AND source_kind!='MOEX_M1_AGGREGATED_M15')"""
        ).fetchone()[0]
        if mixed:
            raise ValueError("archive contains mixed, wrong-board or unknown candles")
        bad_captures = con.execute(
            """SELECT count(*) FROM research_intraday_captured_days
               WHERE symbol!='SBER' OR secid!='SBER'
                  OR quality NOT IN ('CAPTURED_WITH_GAPS','CAPTURED_SOURCE_RESPONSE')"""
        ).fetchone()[0]
        if bad_captures:
            raise ValueError("unexpected research capture identity/quality")
        missing_quality = con.execute(
            """SELECT count(*) FROM research_intraday_candles c
               LEFT JOIN research_intraday_captured_days d
                 ON c.symbol=d.symbol AND c.secid=d.secid
                    AND c.trade_date=d.trade_date
               WHERE d.quality IS NULL"""
        ).fetchone()[0]
        if missing_quality:
            raise ValueError("research candles without immutable day checkpoints")
        duplicates = con.execute(
            """SELECT count(*) FROM (
                 SELECT secid,timeframe,begin FROM research_intraday_candles
                 GROUP BY 1,2,3 HAVING count(*)>1
               )"""
        ).fetchone()[0]
        if duplicates:
            raise ValueError("duplicate research candle keys")

    def inventory(self) -> dict[str, int]:
        self._verify_file_hash()
        result = dict(self.connection.execute(
            "SELECT timeframe,count(*) FROM research_intraday_candles "
            "GROUP BY timeframe ORDER BY timeframe"
        ).fetchall())
        self._verify_file_hash()
        return result

    def read(
        self, *, timeframe: str, from_civil_date: str,
        through_civil_date: str, max_bars: int = 1000,
    ) -> SberResearchRead:
        if timeframe not in _SOURCE:
            raise ValueError("research reader supports SBER H1/M15 only")
        for val in (from_civil_date, through_civil_date):
            if date.fromisoformat(val).isoformat() != val:
                raise ValueError("strict ISO civil date YYYY-MM-DD required")
        if through_civil_date < from_civil_date:
            raise ValueError("reversed research interval")
        require_historical_intraday_scope(
            timeframe, from_civil_date, through_civil_date
        )
        if type(max_bars) is not int or not 1 <= max_bars <= 2000:
            raise ValueError("max_bars must be an integer in 1..2000")
        self._verify_file_hash()
        con = self.connection
        qualities = con.execute(
            """SELECT trade_date,quality,h1_rows,m15_rows
               FROM research_intraday_captured_days
               WHERE symbol='SBER' AND secid='SBER'
                 AND trade_date BETWEEN ? AND ?
               ORDER BY trade_date""",
            [from_civil_date, through_civil_date],
        ).fetchall()
        # Rows from a day without a checkpoint are never treated as
        # independent good research history.
        ledger = {r[0]: r[1] for r in qualities}
        records = con.execute(
            """SELECT c.begin,c.end_time,c.trade_date,c.open,c.high,c.low,
                      c.close,c.volume,c.value,c.source_kind,
                      c.normalized_sha256,d.quality
               FROM research_intraday_candles c
               JOIN research_intraday_captured_days d
                 ON d.symbol=c.symbol AND d.secid=c.secid
                    AND d.trade_date=c.trade_date
               WHERE c.symbol='SBER' AND c.secid='SBER'
                 AND c.engine='stock' AND c.market='shares'
                 AND c.board='TQBR' AND c.timeframe=?
                 AND c.trade_date BETWEEN ? AND ?
               ORDER BY c.begin LIMIT ?""",
            [timeframe, from_civil_date, through_civil_date, max_bars + 1],
        ).fetchall()
        if len(records) > max_bars:
            raise ValueError("requested read exceeds max_bars; split by dates")
        if not records:
            raise ValueError("no captured research candles for requested scope")
        bars: list[SberResearchBar] = []
        previous_begin: datetime | None = None
        gap_candidates: list[tuple[str, str, str]] = []
        for r in records:
            begin, end, civil_date = r[0], r[1], r[2]
            stamp = datetime.fromisoformat(begin)
            until = datetime.fromisoformat(end)
            if (stamp.utcoffset() != timedelta(hours=3)
                    or until.utcoffset() != timedelta(hours=3)
                    or stamp.date().isoformat() != civil_date
                    or (until - stamp).total_seconds()
                       != (3599 if timeframe == "H1" else 899)
                    or (timeframe == "M15" and
                        (stamp.minute % 15 or stamp.second or stamp.microsecond))
                    or (timeframe == "H1" and
                        (stamp.minute or stamp.second or stamp.microsecond))):
                raise ValueError("untrusted stored research timestamps")
            values = (r[3], r[4], r[5], r[6])
            if (any(not isinstance(x, (float, int))
                    or not math.isfinite(x) or x <= 0 for x in values)
                    or not r[5] <= min(r[3], r[6])
                    <= max(r[3], r[6]) <= r[4]):
                raise ValueError("invalid immutable OHLC")
            if any(x is not None and (not math.isfinite(x) or x < 0)
                   for x in (r[7], r[8])):
                raise ValueError("invalid research volume/value")
            if r[9] != _SOURCE[timeframe] or not _DIGEST.fullmatch(r[10]):
                raise ValueError("invalid source identity or fingerprint")
            if civil_date not in ledger or ledger[civil_date] != r[11]:
                raise ValueError("capture quality conflict")
            if previous_begin is not None:
                if stamp <= previous_begin:
                    raise ValueError("unsorted or duplicate candle begin")
                if bars[-1].civil_date == civil_date:
                    expected = (900 if timeframe == "M15" else 3600)
                    if (stamp - previous_begin).total_seconds() > expected:
                        gap_candidates.append(
                            (civil_date, bars[-1].end, begin)
                        )
            previous_begin = stamp
            bars.append(SberResearchBar(
                record_key=f"SBER|{timeframe}|SBER|{begin}",
                timeframe=timeframe, civil_date=civil_date,
                begin=begin, end=end, open=r[3], high=r[4],
                low=r[5], close=r[6], volume=r[7], value=r[8],
                source_kind=r[9], source_fingerprint=r[10],
                capture_day_quality=r[11],
            ))
        self._verify_file_hash()
        return SberResearchRead(
            archive_sha256=self.archive_sha256,
            timeframe=timeframe,
            from_civil_date=from_civil_date,
            through_civil_date=through_civil_date,
            bars=tuple(bars),
            capture_days=tuple((r[0], r[1]) for r in qualities),
            gap_candidates=tuple(gap_candidates),
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SberResearchReadOnly:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
