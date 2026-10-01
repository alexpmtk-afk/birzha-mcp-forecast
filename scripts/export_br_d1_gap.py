from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from birzha.application.market_data import MarketDataService


SYMBOL = "BR"
RAW_COLUMNS = [
    "record_key","calendar_date","trade_session_date","session_id",
    "bar_start_time","bar_end_time","available_at","available_at_confidence",
    "contract_code","open","high","low","close","volume","number_of_trades",
    "turnover","is_complete","source_interval","source_candles","source_id",
]
INDICATOR_COLUMNS = [
    "record_key","calendar_date","trade_session_date","bar_start_time","bar_end_time",
    "contract_code","timeframe","close","volume","return_5","return_10","return_20",
    "sma_20","sma_50","efficiency_ratio_20","atr_14_pct","volume_ratio_20",
    "vwap_20","price_location_20","support_20","resistance_20","trend_score",
    "source_id","calculated_at",
]


def _indicator_row(candles, i: int, *, key: str, secid: str, calculated_at: str):
    closes=[c.close for c in candles[:i+1]]
    volumes=[c.volume for c in candles[:i+1]]
    def ret(n):
        if len(closes)<=n or closes[-1] is None or closes[-n-1] in (None,0): return None
        return closes[-1]/closes[-n-1]-1
    def sma(n):
        if len(closes)<n: return None
        w=closes[-n:]
        if any(v is None for v in w): return None
        return sum(w)/n
    def er(n):
        if len(closes)<=n: return None
        w=closes[-(n+1):]
        if any(v is None for v in w): return None
        direction=abs(w[-1]-w[0])
        noise=sum(abs(b-a) for a,b in zip(w,w[1:]))
        return direction/noise if noise else 0.0
    def atr_pct(n):
        w=candles[max(0,i-n):i+1]
        if len(w)<n+1 or any(c.high is None or c.low is None or c.close is None for c in w): return None
        trs=[max(c.high-c.low,abs(c.high-p.close),abs(c.low-p.close)) for p,c in zip(w[:-1],w[1:])]
        return (sum(trs)/len(trs))/w[-1].close if w[-1].close else None
    def volume_ratio(n):
        if len(volumes)<n+1 or volumes[-1] is None: return None
        base=volumes[-(n+1):-1]
        if any(v is None for v in base): return None
        avg=sum(base)/n
        return volumes[-1]/avg if avg else None

    w20=candles[max(0,i-19):i+1]
    weighted=0.0
    volsum=0.0
    for c in w20:
        price=(c.high+c.low+c.close)/3 if c.high is not None and c.low is not None and c.close is not None else c.close
        if price is None or c.volume is None or c.volume<=0: continue
        weighted += price*c.volume
        volsum += c.volume
    vwap=weighted/volsum if volsum else None
    highs=[c.high for c in w20 if c.high is not None]
    lows=[c.low for c in w20 if c.low is not None]
    cs=[c.close for c in w20 if c.close is not None]
    support=min(lows) if lows else None
    resistance=max(highs) if highs else None
    location=None
    if highs and lows and cs:
        location=0.5 if resistance==support else (cs[-1]-support)/(resistance-support)

    s20=sma(20); s50=sma(50); r20=ret(20); e20=er(20)
    trend=0.0
    last=closes[-1] if closes else None
    if last is not None:
        if s20 is not None: trend += 1.0 if last>s20 else -1.0
        if s20 is not None and s50 is not None: trend += 1.0 if s20>s50 else -1.0
        if r20 is not None: trend += 1.0 if r20>0 else -1.0
        if e20 is not None: trend *= 0.5+min(1.0,e20)

    c=candles[i]
    r6=lambda v: None if v is None else round(float(v),6)
    return [
        key+"|IND",c.begin[:10],c.begin[:10],c.begin,c.end,secid,"D1",c.close,c.volume,
        r6(ret(5)),r6(ret(10)),r6(r20),r6(s20),r6(s50),r6(e20),r6(atr_pct(14)),
        r6(volume_ratio(20)),r6(vwap),r6(location),r6(support),r6(resistance),r6(trend),
        "DERIVED_FROM_D1",calculated_at,
    ]


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--from-date", required=True)
    p.add_argument("--till-date", required=True)
    p.add_argument("--output", required=True)
    args=p.parse_args()
    start=date.fromisoformat(args.from_date)
    finish=date.fromisoformat(args.till_date)
    if finish<start:
        raise ValueError("till-date before from-date")

    service=MarketDataService.default()
    resolver=service.historical_future_resolver
    assert resolver is not None
    timeline=list(resolver.timeline(SYMBOL,start,finish))
    if not timeline:
        raise RuntimeError("no BR futures sessions resolved")

    session_rows=[[SYMBOL,inst.secid,day.isoformat()] for day,inst in timeline]
    instruments={}
    for _,inst in timeline:
        instruments[inst.secid]=inst

    raw=[]
    indicators=[]
    per_contract={}
    for secid,inst in sorted(instruments.items()):
        context_start=start-timedelta(days=120)
        series=service.candles_for_instrument(
            inst,timeframe="D1",
            from_date=context_start.isoformat(),till_date=finish.isoformat(),
            completed_only=True,
        )
        candles=list(series.candles)
        count=0
        for i,c in enumerate(candles):
            d=date.fromisoformat(c.begin[:10])
            if not (start<=d<=finish):
                continue
            key=f"{SYMBOL}|D1|{secid}|{c.begin}"
            raw.append([
                key,d.isoformat(),d.isoformat(),"",c.begin,c.end,c.end,"INFERRED",secid,
                c.open,c.high,c.low,c.close,c.volume,"",c.value,bool(c.completed),"D1",1,series.source,
            ])
            indicators.append(_indicator_row(candles,i,key=key,secid=secid,calculated_at=finish.isoformat()))
            count+=1
        per_contract[secid]=count

    raw.sort(key=lambda r:(r[1],r[8],r[4]))
    indicators.sort(key=lambda r:(r[1],r[5],r[3]))
    out=Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    def dump(name,payload):
        (out/name).write_text(json.dumps(payload,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    dump("D1_GAP.json",{"headers":RAW_COLUMNS,"rows":raw})
    dump("D1_GAP_INDICATORS.json",{"headers":INDICATOR_COLUMNS,"rows":indicators})
    dump("SESSIONS_GAP.json",{"headers":["symbol","secid","trade_date"],"rows":session_rows})
    summary={
        "symbol":SYMBOL,"from_date":start.isoformat(),"till_date":finish.isoformat(),
        "session_rows":len(session_rows),"raw_rows":len(raw),"indicator_rows":len(indicators),
        "contracts":per_contract,
    }
    dump("D1_GAP_SUMMARY.json",summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
