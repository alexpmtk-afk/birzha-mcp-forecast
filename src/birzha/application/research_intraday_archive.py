"""Isolated immutable-price H1/M15 research archive, never the production D1 store.

Store only native H1 and strictly constructed M15; raw one-minute source rows
are transient and deliberately not persisted. No PIT/first-receipt claim.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from birzha.application.history_policy import require_historical_intraday_scope
from birzha.domain.market import Candle, CandleSeries, Instrument

_MOSCOW = ZoneInfo("Europe/Moscow")
_ARCHIVE_ID = "BIRZHA_RESEARCH_INTRADAY_2025_2026_V1"
_SOURCE_KIND = {"H1": "MOEX_NATIVE_H1", "M15": "MOEX_M1_AGGREGATED_M15"}
_MAX_BATCH = 1500


def _moment(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("explicit candle time required")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (moment.replace(tzinfo=_MOSCOW) if moment.tzinfo is None
            else moment.astimezone(_MOSCOW))


def _fingerprint(candle: Candle) -> str:
    # Normalized observation fingerprint, NOT original MOEX first-receipt evidence.
    value = {field: getattr(candle, field) for field in
             ("open", "high", "low", "close", "value", "volume", "begin", "end", "completed")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _check(candle: Candle, timeframe: str) -> tuple[str, str, str]:
    if not isinstance(candle, Candle) or candle.completed is not True:
        raise ValueError("only explicitly completed Candle is accepted")
    begin, end = _moment(candle.begin), _moment(candle.end)
    duration = (end - begin).total_seconds()
    expected = 3600 if timeframe == "H1" else 900
    if duration not in (expected, expected - 1):
        raise ValueError("H1/M15 candle must span its exact timeframe")
    if timeframe == "M15" and (begin.minute % 15 or begin.second or begin.microsecond):
        raise ValueError("M15 start must align to Moscow quarter-hour")
    if timeframe == "H1" and (begin.minute or begin.second or begin.microsecond):
        raise ValueError("H1 start must align to Moscow hour")
    start_day, end_day = begin.date().isoformat(), end.date().isoformat()
    require_historical_intraday_scope(timeframe, start_day, end_day)
    ohlc = (candle.open, candle.high, candle.low, candle.close)
    if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in ohlc):
        raise ValueError("finite positive OHLC required")
    if not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high:
        raise ValueError("inconsistent OHLC")
    for v in (candle.value, candle.volume):
        if v is not None and (type(v) not in (int, float) or not math.isfinite(v) or v < 0):
            raise ValueError("finite nonnegative value/volume required")
    return begin.isoformat(), end.isoformat(), start_day


class ResearchIntradayArchive:
    """Open only a dedicated archive; refuse to modify an unmarked DuckDB file."""

    def __init__(self, path: str):
        if path != ":memory:":
            target = Path(path)
            if target.suffix != ".duckdb" or target.is_symlink() or not target.parent.is_dir():
                raise ValueError("existing parent, regular dedicated .duckdb path required")
            if target.exists() and not target.is_file():
                raise ValueError("archive path must be a regular file")
            existed = target.exists()
        else:
            existed = False
        self.path = path
        # Inspect any existing file strictly READ-ONLY before opening a
        # write connection; never touch production or an unmarked database.
        if existed:
            inspection = duckdb.connect(path, read_only=True)
            try:
                present = inspection.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name='research_intraday_meta'"
                ).fetchone()[0]
                if not present:
                    raise ValueError("refusing existing database without research archive marker")
                marker = inspection.execute(
                    "SELECT value FROM research_intraday_meta WHERE key='archive_id'"
                ).fetchone()
                if marker != (_ARCHIVE_ID,):
                    raise ValueError("research archive marker mismatch")
            finally:
                inspection.close()
        self.conn = duckdb.connect(path)
        try:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS research_intraday_meta (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)"
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO research_intraday_meta VALUES ('archive_id', ?)",
                [_ARCHIVE_ID],
            )
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS research_intraday_candles (
                    symbol VARCHAR NOT NULL,
                    secid VARCHAR NOT NULL,
                    engine VARCHAR NOT NULL,
                    market VARCHAR NOT NULL,
                    board VARCHAR NOT NULL,
                    timeframe VARCHAR NOT NULL,
                    begin VARCHAR NOT NULL,
                    end_time VARCHAR NOT NULL,
                    trade_date VARCHAR NOT NULL,
                    open DOUBLE NOT NULL,
                    high DOUBLE NOT NULL,
                    low DOUBLE NOT NULL,
                    close DOUBLE NOT NULL,
                    volume DOUBLE,
                    value DOUBLE,
                    source_kind VARCHAR NOT NULL,
                    normalized_sha256 VARCHAR NOT NULL,
                    PRIMARY KEY (secid, timeframe, begin)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS research_intraday_captured_days (
                    symbol VARCHAR NOT NULL,
                    secid VARCHAR NOT NULL,
                    trade_date VARCHAR NOT NULL,
                    h1_rows INTEGER NOT NULL,
                    m15_rows INTEGER NOT NULL,
                    raw_m1_rows INTEGER NOT NULL,
                    quality VARCHAR NOT NULL,
                    incomplete_m15_buckets INTEGER NOT NULL,
                    skipped_h1_bars INTEGER NOT NULL,
                    captured_at VARCHAR NOT NULL,
                    PRIMARY KEY (symbol, secid, trade_date)
                )
            """)
        except Exception:
            self.conn.close()
            raise

    def close(self) -> None:
        self.conn.close()

    def ingest(self, series: CandleSeries) -> dict[str, object]:
        """Atomic append or exact reuse. Conflicting bars reject the entire batch."""
        if not isinstance(series, CandleSeries) or series.timeframe not in _SOURCE_KIND:
            raise ValueError("only H1 and M15 research series accepted")
        instrument = series.instrument
        if not isinstance(instrument, Instrument) or not all((
            instrument.symbol, instrument.secid, instrument.engine,
            instrument.market, instrument.board,
        )):
            raise ValueError("exact MOEX instrument identity required")
        if instrument.symbol not in {"SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"}:
            raise ValueError("market not in approved six-market pilot")
        if not (0 < len(series.candles) <= _MAX_BATCH):
            raise ValueError("bounded nonempty research batch required")
        staged, keys, prev_end = [], set(), None
        for candle in series.candles:
            begin, end, trade_date = _check(candle, series.timeframe)
            key = (instrument.secid, series.timeframe, begin)
            if key in keys or (prev_end is not None and begin < prev_end):
                raise ValueError("duplicate or overlapping input candles")
            keys.add(key)
            prev_end = end
            staged.append((instrument.symbol, instrument.secid,
                           instrument.engine, instrument.market, instrument.board,
                           series.timeframe, begin, end, trade_date,
                           candle.open, candle.high, candle.low, candle.close,
                           candle.volume, candle.value, _SOURCE_KIND[series.timeframe],
                           _fingerprint(candle)))
        inserted, existing = 0, 0
        self.conn.execute("BEGIN TRANSACTION")
        try:
            for row in staged:
                old = self.conn.execute("""
                    SELECT symbol, engine, market, board, end_time, trade_date,
                           open, high, low, close, volume, value, source_kind,
                           normalized_sha256
                    FROM research_intraday_candles
                    WHERE secid=? AND timeframe=? AND begin=?
                """, [row[1], row[5], row[6]]).fetchone()
                comparison = (row[0], row[2], row[3], row[4], row[7], row[8],
                              *row[9:])
                if old is not None:
                    if old != comparison:
                        raise ValueError("conflicting immutable research candle: different value/source")
                    existing += 1
                else:
                    self.conn.execute(
                        "INSERT INTO research_intraday_candles VALUES (" +
                        ",".join("?" for _ in row) + ")", row,
                    )
                    inserted += 1
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return {"symbol": instrument.symbol, "secid": instrument.secid,
                "timeframe": series.timeframe, "inserted": inserted,
                "reused": existing, "total_rows": len(staged),
                "historical_first_receipt": "NOT_PROVEN"}

    def read_exact(self, instrument: Instrument, *, timeframe: str,
                   from_date: str, till_date: str) -> CandleSeries:
        if timeframe not in _SOURCE_KIND:
            raise ValueError("only H1/M15 research reads are supported")
        require_historical_intraday_scope(timeframe, from_date, till_date)
        if not instrument.secid or not instrument.symbol:
            raise ValueError("exact instrument required")
        rows = self.conn.execute("""
            SELECT symbol, engine, market, board, open, close, high, low,
                   value, volume, begin, end_time
            FROM research_intraday_candles
            WHERE secid=? AND timeframe=? AND trade_date BETWEEN ? AND ?
            ORDER BY begin
        """, [instrument.secid, timeframe, from_date[:10], till_date[:10]]).fetchall()
        candles = []
        for r in rows:
            if (r[0], r[1], r[2], r[3]) != (
                instrument.symbol, instrument.engine, instrument.market, instrument.board
            ):
                raise ValueError("stored contract identity mismatch")
            candles.append(Candle(
                open=r[4], close=r[5], high=r[6], low=r[7],
                value=r[8], volume=r[9], begin=r[10], end=r[11],
                completed=True, available_at=r[11],
                available_at_confidence="INFERRED", source="RESEARCH_INTRADAY_ARCHIVE",
            ))
        return CandleSeries(instrument, timeframe, tuple(candles),
                            source="RESEARCH_INTRADAY_ARCHIVE")

    def _day_counts(self, secid: str, trade_date: str) -> tuple[int, int]:
        rows = self.conn.execute("""
            SELECT timeframe, count(*) FROM research_intraday_candles
            WHERE secid=? AND trade_date=?
            GROUP BY timeframe
        """, [secid, trade_date]).fetchall()
        counts = dict(rows)
        return int(counts.get("H1", 0)), int(counts.get("M15", 0))

    def capture_quality(self, instrument: Instrument, trade_date: str) -> str | None:
        """CAPTURED_WITH_GAPS remains non-certified, even if bars exist."""
        require_historical_intraday_scope("H1", trade_date, trade_date)
        row = self.conn.execute("""
            SELECT h1_rows, m15_rows, quality FROM research_intraday_captured_days
            WHERE symbol=? AND secid=? AND trade_date=?
        """, [instrument.symbol, instrument.secid, trade_date]).fetchone()
        if row is None:
            return None
        if self._day_counts(instrument.secid, trade_date) != tuple(row[:2]):
            raise ValueError("corrupt research day checkpoint: stored bars mismatch")
        return str(row[2])

    def is_captured(self, instrument: Instrument, trade_date: str) -> bool:
        return self.capture_quality(instrument, trade_date) is not None

    def mark_captured(self, instrument: Instrument, trade_date: str, *,
                      h1_rows: int, m15_rows: int, raw_m1_rows: int,
                      incomplete_m15_buckets: int = 0,
                      skipped_h1_bars: int = 0) -> None:
        """Store source response and gap counts; never claim independent full session."""
        require_historical_intraday_scope("M15", trade_date, trade_date)
        if (min(h1_rows, m15_rows, raw_m1_rows) <= 0 or
                min(incomplete_m15_buckets, skipped_h1_bars) < 0 or
                self._day_counts(instrument.secid, trade_date) != (h1_rows, m15_rows)):
            raise ValueError("capture checkpoint requires both stored H1 and M15 evidence")
        quality = ("CAPTURED_WITH_GAPS" if incomplete_m15_buckets or skipped_h1_bars
                   else "CAPTURED_SOURCE_RESPONSE")
        old_quality = self.capture_quality(instrument, trade_date)
        if old_quality == "CAPTURED_SOURCE_RESPONSE":
            return
        if old_quality is not None:
            if old_quality != "CAPTURED_WITH_GAPS":
                raise ValueError("unknown research capture quality")
            self.conn.execute("""
                UPDATE research_intraday_captured_days SET
                    h1_rows=?, m15_rows=?, raw_m1_rows=?, quality=?,
                    incomplete_m15_buckets=?, skipped_h1_bars=?, captured_at=?
                WHERE symbol=? AND secid=? AND trade_date=?
            """, [h1_rows, m15_rows, raw_m1_rows, quality, incomplete_m15_buckets,
                  skipped_h1_bars, datetime.now(timezone.utc).isoformat(),
                  instrument.symbol, instrument.secid, trade_date])
        else:
            self.conn.execute("""
                INSERT INTO research_intraday_captured_days VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                instrument.symbol, instrument.secid, trade_date,
                h1_rows, m15_rows, raw_m1_rows, quality,
                incomplete_m15_buckets, skipped_h1_bars,
                datetime.now(timezone.utc).isoformat(),
            ])

    def inventory(self) -> list[dict[str, object]]:
        rows = self.conn.execute("""
            SELECT symbol, secid, timeframe, count(*), min(trade_date), max(trade_date)
            FROM research_intraday_candles
            GROUP BY symbol, secid, timeframe
            ORDER BY symbol, secid, timeframe
        """).fetchall()
        return [dict(zip(("symbol", "secid", "timeframe", "rows", "first_day", "last_day"), row))
                for row in rows]
