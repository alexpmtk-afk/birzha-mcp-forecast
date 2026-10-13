"""Recover REAL observed (not synthesized) shorter IMOEX H1 and partial M15.

Canonical full H1/M15 stay immutable. This reconstructs secondary LIVE-compatible
source observations from native MOEX H1 and M1, records input minute counts,
and verifies every previously archived strict bar matches the new source.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from birzha.application.history_policy import require_historical_intraday_scope
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import _moment
from birzha.providers.moex_iss import MoexIssClient

MOSCOW=ZoneInfo("Europe/Moscow")
SOURCE_SHA="caa235288d442cf88fd9f8ae38a5c51071042badb6b9295386495468aaa2d4c6"


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def same_price(row, candle):
    for field in ("open","close","high","low","volume","value"):
        a=row[field]
        b=getattr(candle,field)
        if a is None or b is None:
            if a is not None or b is not None:
                return False
        elif not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-7):
            return False
    return (row["begin"]==_moment(candle.begin).isoformat() and
            row["end_time"]==_moment(candle.end).isoformat())


def capture_day(cx,source,instrument,day):
    require_historical_intraday_scope("H1",day,day)
    record=cx.execute("""
       SELECT h1_rows,m15_rows,incomplete_m15_buckets,skipped_h1_bars
       FROM research_intraday_captured_days
       WHERE symbol='IMOEX' AND secid='IMOEX' AND trade_date=?
    """,[day]).fetchone()
    if record is None:
        raise ValueError("missing canonical source checkpoint")
    prior={}
    for tf in ("H1","M15"):
        cols=("begin","end_time","open","close","high","low","volume","value")
        rows=cx.execute("""
          SELECT begin,end_time,open,close,high,low,volume,value
          FROM research_intraday_candles
          WHERE secid='IMOEX' AND timeframe=? AND trade_date=?
        """,[tf,day]).fetchall()
        prior[tf]={r[0]:dict(zip(cols,r)) for r in rows}
    if (len(prior["H1"]),len(prior["M15"]))!=tuple(record[:2]):
        raise ValueError("canonical source checkpoint row counts mismatch")

    h1=source.candles_for_instrument(
        instrument,timeframe="H1",from_date=day,till_date=day,completed_only=True)
    m1=source.candles_for_instrument(
        instrument,timeframe="M1",from_date=day,till_date=day,completed_only=True)
    if h1.instrument!=instrument or m1.instrument!=instrument:
        raise ValueError("source exact market contract drift")
    cutoff=datetime(2026,12,31,23,59,tzinfo=MOSCOW)
    live=MoexIssClient._aggregate_m15(tuple(m1.candles),as_of=cutoff)

    buckets={}
    for bar in m1.candles:
        begin=_moment(bar.begin)
        if begin.date().isoformat()!=day:
            continue
        if begin.second or begin.microsecond:
            raise ValueError("unaligned source minute")
        if bar.completed is not True or (_moment(bar.end)-begin).total_seconds() not in (59,60):
            raise ValueError("invalid native minute duration")
        bucket=begin.replace(minute=(begin.minute//15)*15,second=0,microsecond=0).isoformat()
        minute=begin.isoformat()
        rows=buckets.setdefault(bucket,set())
        if minute in rows:
            raise ValueError("duplicated source M1 minute")
        rows.add(minute)
    supplemental=[]
    old_seen={"H1":set(),"M15":set()}
    source_full={"H1":0,"M15":0}
    for bar in h1.candles:
        start=_moment(bar.begin)
        end=_moment(bar.end)
        if start.date().isoformat()!=day:
            continue
        duration=(end-start).total_seconds()
        if bar.completed is not True or not 0 < duration <= 3600:
            raise ValueError("invalid native H1 observed range")
        key=start.isoformat()
        if duration in (3599,3600) and start.minute==0 and start.second==0:
            stored=prior["H1"].get(key)
            if stored is None or not same_price(stored,bar):
                raise ValueError("canonical H1 differs from refetched MOEX native")
            if key in old_seen["H1"]:
                raise ValueError("duplicate H1")
            old_seen["H1"].add(key)
            source_full["H1"]+=1
        else:
            supplemental.append(pack("H1",bar,"NATIVE_H1_SHORT_OBSERVED",None))
    for bar in live:
        start=_moment(bar.begin)
        if start.date().isoformat()!=day:
            continue
        if bar.completed is not True:
            raise ValueError("old historical M15 observed as incomplete")
        if start.minute%15 or start.second:
            raise ValueError("M15 alignment invalid")
        key=start.isoformat()
        count=len(buckets.get(key,set()))
        if count<1 or count>15:
            raise ValueError("unsupported source minute count")
        if count==15:
            # A fully present bucket must agree with the original strict archive.
            stored=prior["M15"].get(key)
            if stored is None or not same_price(stored,bar):
                raise ValueError("canonical M15 differs from refetched MOEX")
            if key in old_seen["M15"]:
                raise ValueError("duplicate M15")
            old_seen["M15"].add(key)
            source_full["M15"]+=1
        else:
            supplemental.append(pack("M15",bar,"SOURCE_M15_WITH_FEWER_THAN_15_M1",count))
    if old_seen["H1"]!=set(prior["H1"]) or old_seen["M15"]!=set(prior["M15"]):
        raise ValueError("canonical strict archive missing in native source replay")
    recovered_h1=sum(r["timeframe"]=="H1" for r in supplemental)
    recovered_m15=sum(r["timeframe"]=="M15" for r in supplemental)
    return {
        "day":day,"canonical_h1":source_full["H1"],
        "canonical_m15":source_full["M15"],
        "native_h1_source":len(h1.candles),
        "minute_rows":len(m1.candles),
        "source_m15_buckets":sum(x["timeframe"]=="M15" for x in supplemental)+source_full["M15"],
        "source_marked_missing_m15":record[2],
        "source_marked_short_h1":record[3],
        "recovered_m15":recovered_m15,"recovered_h1":recovered_h1,
        "unresolved_m15_reported":max(0,record[2]-recovered_m15),
        "unresolved_h1_reported":max(0,record[3]-recovered_h1),
        "supplemental":supplemental,
    }


def pack(tf,bar,quality,minute_count):
    start,end=_moment(bar.begin),_moment(bar.end)
    require_historical_intraday_scope(tf,start.date().isoformat(),end.date().isoformat())
    vals=(bar.open,bar.high,bar.low,bar.close)
    if any(type(v) not in (float,int) or not math.isfinite(v) or v<=0 for v in vals):
        raise ValueError("invalid real source OHLC")
    if not bar.low<=min(bar.open,bar.close)<=max(bar.open,bar.close)<=bar.high:
        raise ValueError("invalid real source OHLC ordering")
    if any(v is not None and (not math.isfinite(v) or v<0) for v in (bar.value,bar.volume)):
        raise ValueError("invalid source amounts")
    return {
        "symbol":"IMOEX","secid":"IMOEX","timeframe":tf,
        "begin":start.isoformat(),"end":end.isoformat(),
        "open":bar.open,"high":bar.high,"low":bar.low,"close":bar.close,
        "value":bar.value,"volume":bar.volume,
        "source_minute_rows":minute_count,
        "quality":quality,"synthetic_price":False,
        "available_at_confidence":"INFERRED",
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--expected-sha256",default=SOURCE_SHA)
    p.add_argument("--year",type=int,required=True)
    p.add_argument("--month",type=int,required=True)
    p.add_argument("--day",help="optional single-day probe YYYY-MM-DD")
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    if args.year not in (2025,2026) or not 1<=args.month<=12 or (
        args.year==2026 and args.month>10
    ):
        p.error("fixed past historical scope only")
    if args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir():
        p.error("output must be a new JSON file in existing directory")
    if sha256(args.archive)!=args.expected_sha256:
        p.error("canonical original DuckDB SHA differs")
    if args.day is not None and (
        date.fromisoformat(args.day).year!=args.year
        or date.fromisoformat(args.day).month!=args.month
    ):
        p.error("day not inside month")
    cx=duckdb.connect(str(args.archive),read_only=True)
    try:
        marker=cx.execute(
           "SELECT value FROM research_intraday_meta WHERE key='archive_id'"
        ).fetchone()
        if marker!=("BIRZHA_RESEARCH_INTRADAY_2025_2026_V1",):
            raise ValueError("wrong research DB marker")
        ym=f"{args.year}-{args.month:02d}"
        days=[r[0] for r in cx.execute("""
           SELECT trade_date FROM research_intraday_captured_days
           WHERE symbol='IMOEX' AND secid='IMOEX' AND trade_date LIKE ?
           ORDER BY trade_date
        """,[ym+"%"]).fetchall()]
        if args.day is not None:
            if args.day not in days:
                raise ValueError("day absent from prior source checkpoints")
            days=[args.day]
        if not days:
            raise ValueError("no exact-contract source checkpoint for month")
        source=MarketDataService.default()
        instrument=source.resolve("IMOEX",as_of=date.fromisoformat(days[-1]))
        if (instrument.symbol,instrument.secid)!=("IMOEX","IMOEX"):
            raise ValueError("incorrect IMOEX source identity")
        outcomes=[]
        for day in days:
            audit=capture_day(cx,source,instrument,day)
            outcomes.append(audit)
            print(json.dumps({k:v for k,v in audit.items() if k!="supplemental"},sort_keys=True),flush=True)
    finally:
        cx.close()
    result={
       "schema":"IMOEX_SOURCE_INTERVAL_RECOVERY_V1",
       "original_archive_sha256":args.expected_sha256,
       "month":f"{args.year}-{args.month:02d}",
       "days":len(outcomes),
       "source_days_match_original":True,
       "source_changes_rejected":True,
       "native_short_h1":sum(x["recovered_h1"] for x in outcomes),
       "partial_m15_from_source":sum(x["recovered_m15"] for x in outcomes),
       "unresolved_h1_reported":sum(x["unresolved_h1_reported"] for x in outcomes),
       "unresolved_m15_reported":sum(x["unresolved_m15_reported"] for x in outcomes),
       "no_invented_prices":True,
       "unproven_exchange_session_full_coverage":True,
       "quality":"LIVE_SOURCE_COMPAT_OBSERVED_NOT_PIT_PROVEN",
       "rows":outcomes,
    }
    args.output.write_text(json.dumps(result,sort_keys=True,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items() if k!="rows"},sort_keys=True))


if __name__=="__main__":
    main()
