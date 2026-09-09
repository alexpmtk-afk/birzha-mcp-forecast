"""Persistent historical candle storage for the Historical Data Foundation."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import duckdb

from birzha.domain.market import Candle, CandleSeries, Instrument


@dataclass(frozen=True, slots=True)
class HistoricalCoverage:
    secid: str
    timeframe: str
    first_begin: str | None
    last_end: str | None
    count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "secid": self.secid,
            "timeframe": self.timeframe,
            "first_begin": self.first_begin,
            "last_end": self.last_end,
            "count": self.count,
        }


class HistoricalCandleStore(Protocol):
    """Storage contract shared by local DuckDB and remote YDB backends."""

    def upsert_series(self, series: CandleSeries) -> int: ...

    def coverage(self, secid: str, timeframe: str) -> HistoricalCoverage: ...

    def read(self, instrument: Instrument, timeframe: str, from_date: str, till_date: str) -> CandleSeries: ...

    def stored_trade_dates(self, secid: str, timeframe: str, from_date: str, till_date: str) -> tuple[str, ...]: ...

    def is_verified(self, symbol: str, timeframe: str, from_date: str, till_date: str) -> bool: ...

    def mark_verified(self, symbol: str, timeframe: str, from_date: str, till_date: str) -> None: ...


class DuckDBHistoricalCandleStore:
    """Idempotent candle store keyed by real contract identity + timeframe + begin."""

    storage_scope = "local"

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(path)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_candles (
                secid VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                root_symbol VARCHAR,
                board VARCHAR NOT NULL,
                engine VARCHAR NOT NULL,
                market VARCHAR NOT NULL,
                asset_class VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                begin VARCHAR NOT NULL,
                end_time VARCHAR NOT NULL,
                open DOUBLE,
                close DOUBLE,
                high DOUBLE,
                low DOUBLE,
                value DOUBLE,
                volume DOUBLE,
                completed BOOLEAN NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY (secid, timeframe, begin)
            )
        """)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_verified_ranges (symbol VARCHAR NOT NULL, timeframe VARCHAR NOT NULL, from_date VARCHAR NOT NULL, till_date VARCHAR NOT NULL, PRIMARY KEY (symbol, timeframe, from_date, till_date))
        """)

    def upsert_series(self, series: CandleSeries) -> int:
        rows = [
            [
                series.instrument.secid,
                series.instrument.symbol,
                series.instrument.root_symbol,
                series.instrument.board,
                series.instrument.engine,
                series.instrument.market,
                series.instrument.asset_class,
                series.timeframe,
                candle.begin,
                candle.end,
                candle.open,
                candle.close,
                candle.high,
                candle.low,
                candle.value,
                candle.volume,
                candle.completed,
                series.source,
            ]
            for candle in series.candles
        ]
        if not rows:
            return 0
        with self._lock:
            self._connection.executemany("""
                INSERT OR REPLACE INTO historical_candles
                (secid, symbol, root_symbol, board, engine, market, asset_class, timeframe,
                 begin, end_time, open, close, high, low, value, volume, completed, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
        return len(rows)

    def coverage(self, secid: str, timeframe: str) -> HistoricalCoverage:
        with self._lock:
            row = self._connection.execute("""
                SELECT min(begin), max(end_time), count(*)
                FROM historical_candles
                WHERE secid = ? AND timeframe = ? AND completed = true
            """, [secid, timeframe]).fetchone()
        if not row or int(row[2]) == 0:
            return HistoricalCoverage(secid, timeframe, None, None, 0)
        return HistoricalCoverage(secid, timeframe, str(row[0]), str(row[1]), int(row[2]))

    def read(self, instrument: Instrument, timeframe: str, from_date: str, till_date: str) -> CandleSeries:
        with self._lock:
            rows = self._connection.execute("""
                SELECT open, close, high, low, value, volume, begin, end_time, completed, source
                FROM historical_candles
                WHERE secid = ? AND timeframe = ?
                  AND begin >= ? AND begin < ?
                ORDER BY begin
            """, [instrument.secid, timeframe, from_date, _exclusive_upper_bound(till_date)]).fetchall()
        candles = tuple(Candle(
            open=row[0], close=row[1], high=row[2], low=row[3], value=row[4], volume=row[5],
            begin=str(row[6]), end=str(row[7]), completed=bool(row[8]),
        ) for row in rows)
        source = str(rows[0][9]) if rows else instrument.source
        return CandleSeries(instrument=instrument, timeframe=timeframe, candles=candles, source=source)

    def stored_trade_dates(self, secid: str, timeframe: str, from_date: str, till_date: str) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute("""
                SELECT DISTINCT substr(begin, 1, 10) AS trade_date
                FROM historical_candles
                WHERE secid = ? AND timeframe = ? AND completed = true
                  AND begin >= ? AND begin < ?
                ORDER BY trade_date
            """, [secid, timeframe, from_date, _exclusive_upper_bound(till_date)]).fetchall()
        return tuple(str(row[0]) for row in rows)

    def is_verified(self, symbol: str, timeframe: str, from_date: str, till_date: str) -> bool:
        with self._lock:
            row=self._connection.execute("SELECT 1 FROM historical_verified_ranges WHERE symbol=? AND timeframe=? AND from_date<=? AND till_date>=? LIMIT 1",[symbol,timeframe,from_date,till_date]).fetchone()
        return row is not None

    def mark_verified(self, symbol: str, timeframe: str, from_date: str, till_date: str) -> None:
        with self._lock:
            self._connection.execute("INSERT OR IGNORE INTO historical_verified_ranges VALUES (?,?,?,?)",[symbol,timeframe,from_date,till_date])

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _exclusive_upper_bound(till_date: str) -> str:
    if len(till_date) == 10:
        return till_date + "T23:59:59.999999"
    return till_date
