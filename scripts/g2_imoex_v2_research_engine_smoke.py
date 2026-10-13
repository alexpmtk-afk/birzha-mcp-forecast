"""Read-only existing Forecast V1 engine smoke using strict vs genuine-source M15/H1.

Neither variant has historically attested first-receipt timestamps. No trade,
journal, LIVE request routing or model weights are changed.
"""
from __future__ import annotations

import argparse
from datetime import date,datetime,timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_source_compat import IMOEXResearchPriceView
from birzha.application.snapshot import MarketSnapshotService
from birzha.domain.market import CandleSeries
from scripts.g2_imoex_source_interval_recovery import sha256

MOSCOW=ZoneInfo("Europe/Moscow")


class ArchiveResearchMarketData:
    def __init__(self,instrument,d1,view,*,source_compatible,cutoff_at):
        self.instrument=instrument
        self.d1=d1
        self.view=view
        self.source_compatible=source_compatible
        self.cutoff_at=cutoff_at
        self.requested=[]

    def resolve(self,symbol,*,as_of=None):
        if symbol!="IMOEX":
            raise ValueError("only exact IMOEX snapshot")
        return self.instrument

    def candles_for_instrument(self,instrument,*,timeframe,from_date,
                               till_date,completed_only=True,**kwargs):
        if instrument!=self.instrument or timeframe not in ("D1","H1","M15"):
            raise ValueError("illegal archive instrument/timeframe request")
        if till_date>self.cutoff_at.date().isoformat():
            raise ValueError("future relative price time forbidden")
        if timeframe=="D1":
            raw=tuple(x for x in self.d1.candles if
                      from_date<=x.begin[:10]<=till_date)
        else:
            series=self.view.read_exact(
               instrument,timeframe=timeframe,from_date=from_date,
               till_date=till_date,source_compatible=self.source_compatible)
            raw=series.candles
        selected=[]
        for c in raw:
            end=datetime.fromisoformat(c.end.replace("Z","+00:00"))
            end=(end.replace(tzinfo=MOSCOW) if end.tzinfo is None else
                 end.astimezone(MOSCOW))
            if c.completed and end<=self.cutoff_at:
                selected.append(c)
        self.requested.append((timeframe,from_date,till_date,len(selected)))
        return CandleSeries(instrument,timeframe,tuple(selected),
                           source="RECONSTRUCTED_SOURCE_COMPAT_RESEARCH")


def compare_at(archive:Path,cutoff:str,market_data:MarketDataService):
    day=date.fromisoformat(cutoff)
    if not date(2025,8,1)<=day<=date(2026,10,12):
        raise ValueError("pilot cutoff outside approved warm research interval")
    cutoff_at=datetime.combine(day,datetime.max.time(),MOSCOW)
    instrument=market_data.resolve("IMOEX",as_of=day)
    if (instrument.symbol,instrument.secid)!=("IMOEX","IMOEX"):
        raise ValueError("original exact security changed")
    d1=market_data.candles_for_instrument(
       instrument,timeframe="D1",
       from_date=(day-timedelta(days=260)).isoformat(),
       till_date=cutoff,completed_only=True)
    if not d1.candles:
        raise ValueError("no eligible D1 source history")
    view=IMOEXResearchPriceView(archive)
    try:
        result={}
        for use_partial,label in ((False,"strict"),(True,"native_source_compatible")):
            adapter=ArchiveResearchMarketData(
                instrument,d1,view,source_compatible=use_partial,
                cutoff_at=cutoff_at)
            snap=MarketSnapshotService(adapter,flow=None).build(
                "IMOEX",as_of_date=cutoff)
            if min(snap.d1.candles,snap.h1.candles,snap.m15.candles)<50:
                raise ValueError("insufficient H1/M15/D1 history for original V1")
            forecast=build_forecast_from_snapshot(snap)
            result[label]={
                "engine_version":forecast.engine_version,
                "snapshot_id":forecast.snapshot_id,
                "d1_bars":snap.d1.candles,
                "h1_bars":snap.h1.candles,
                "m15_bars":snap.m15.candles,
                "snapshot_as_of":snap.as_of,
                "forecast_payload":forecast.to_dict(),
                "future_source_data_used":False,
                "source_first_receipt_attested":False,
                "operational_forecast_issued":False,
            }
        return result
    finally:
        view.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--expected-sha256",required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--cutoffs",nargs="+",default=["2025-09-15","2026-04-15"])
    args=p.parse_args(argv)
    if (args.output.exists() or args.output.is_symlink() or
        not args.output.parent.is_dir()):
        p.error("new research-only output path required")
    if sha256(args.archive)!=args.expected_sha256:
        p.error("source V2 SHA256 does not match")
    md=MarketDataService.default()
    rows={day:compare_at(args.archive,day,md) for day in args.cutoffs}
    proof={
       "schema":"IMOEX_ACTUAL_SOURCE_COMPAT_FORECAST_V1_PRICE_PATH_V1",
       "archive_sha256":args.expected_sha256,
       "results":rows,"engine_unchanged":True,
       "live_forecast_issued":False,"trade_allowed":False,
       "PIT_source_first_receipt_verified":False,
       "note":"historically reconstructed prices; not certified historic Forecast V1 decisions",
    }
    args.output.write_text(json.dumps(proof,sort_keys=True,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({
        "status":"RECONSTRUCTED_SOURCE_COMPAT_RESEARCH_ONLY",
        "cutoffs":list(rows),
        "comparisons":{day:{
            "strict_M15":v["strict"]["m15_bars"],
            "compatible_M15":v["native_source_compatible"]["m15_bars"],
            "strict_H1":v["strict"]["h1_bars"],
            "compatible_H1":v["native_source_compatible"]["h1_bars"],
        } for day,v in rows.items()},
        "release_allowed":False,
    },sort_keys=True))


if __name__=="__main__":
    main()
