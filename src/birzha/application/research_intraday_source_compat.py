"""Explicit read-only select of STRICT vs MOEX-source-compatible research candles.

Never silently blends a quality-degraded M15 into strict data. Supplementary
rows retain original abbreviated H1 boundaries and M15 source minute counts.
No historical first-receipt is claimed.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from birzha.application.history_policy import require_historical_intraday_scope
from birzha.domain.market import Candle, CandleSeries, Instrument


class IMOEXResearchPriceView:
    def __init__(self,db:Path):
        if not db.is_file() or db.is_symlink():
            raise ValueError("explicit regular isolated research DuckDB required")
        self.conn=duckdb.connect(str(db),read_only=True)
        try:
            marker=self.conn.execute("""
                SELECT value FROM research_intraday_meta
                WHERE key='source_compatible_exception_extension'
            """).fetchone()
            if marker!=("MOEX_SOURCE_OBSERVED_V1",):
                raise ValueError("V2 source quality marker absent")
        except Exception:
            self.conn.close()
            raise

    def close(self):
        self.conn.close()

    def read_exact(self,instrument:Instrument,*,timeframe:str,from_date:str,
                   till_date:str,source_compatible:bool=False)->CandleSeries:
        if (instrument.symbol,instrument.secid)!=("IMOEX","IMOEX"):
            raise ValueError("only exact IMOEX index is admitted")
        if timeframe not in ("H1","M15"):
            raise ValueError("only research H1 and M15 supported")
        if type(source_compatible) is not bool:
            raise ValueError("source_compatible must be explicit boolean")
        require_historical_intraday_scope(timeframe,from_date,till_date)
        rows=self.conn.execute("""
            SELECT engine,market,board,begin,end_time,open,high,low,close,
                   volume,value,source_kind
            FROM research_intraday_candles
            WHERE secid='IMOEX' AND timeframe=?
              AND trade_date BETWEEN ? AND ?
        """,[timeframe,from_date,till_date]).fetchall()
        candles=[]
        for row in rows:
            engine,market,board,begin,end,o,h,l,c,volume,value,source=row
            if (engine,market,board)!=(instrument.engine,instrument.market,instrument.board):
                raise ValueError("canonical instrument identity does not match")
            candles.append(Candle(
                open=o,high=h,low=l,close=c,value=value,volume=volume,
                begin=begin,end=end,completed=True,
                available_at=end,available_at_confidence="INFERRED",
                source=source,
            ))
        if source_compatible:
            extras=self.conn.execute("""
               SELECT begin,end_time,open,high,low,close,volume,value,
                      quality,source_minute_rows,available_at_confidence
               FROM research_intraday_source_observed_exceptions
               WHERE secid='IMOEX' AND timeframe=?
                 AND trade_date BETWEEN ? AND ?
            """,[timeframe,from_date,till_date]).fetchall()
            for begin,end,o,h,l,c,volume,value,quality,count,confidence in extras:
                source=quality+(f"_M1_OBSERVED_{count}" if count is not None else "")
                candles.append(Candle(
                    open=o,high=h,low=l,close=c,volume=volume,value=value,
                    begin=begin,end=end,completed=True,available_at=end,
                    available_at_confidence=confidence,
                    source=source,
                ))
        candles.sort(key=lambda x:x.begin)
        if len({x.begin for x in candles})!=len(candles):
            raise ValueError("duplicate source minute/hour origin in chosen dataset")
        return CandleSeries(instrument,timeframe,tuple(candles),source=(
            "MOEX_RESEARCH_LIVE_SOURCE_COMPAT_PIT_UNPROVEN"
            if source_compatible else "MOEX_RESEARCH_STRICT_SOURCE_ONLY"
        ))

    def quality_counts(self)->dict[str,dict[str,int]]:
        rows=self.conn.execute("""
           SELECT timeframe,quality,count(*)
           FROM research_intraday_source_observed_exceptions
           GROUP BY timeframe,quality
        """).fetchall()
        out={}
        for timeframe,quality,count in rows:
            out.setdefault(timeframe,{})[quality]=count
        return out
