"""BR monthly isolated research capture: existing MOEX resolver, governor and strict M15 only.
Not a production import, no new market-bar aggregation algorithm.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from datetime import date,timedelta,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import duckdb
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.research_intraday_fetch import prepare_one_day
from birzha.application.history_policy import require_historical_intraday_scope

MOSCOW=ZoneInfo("Europe/Moscow")
ARCHIVE="birzha_intraday_research_2025_2026.duckdb"
def sha(p):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""):h.update(b)
    return h.hexdigest()
def run(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--year",type=int,required=True)
    p.add_argument("--month",type=int,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    if args.year not in (2025,2026) or not 1<=args.month<=12:
        p.error("scope 2025/2026 only")
    start=date(args.year,args.month,1)
    finish=(date(args.year+(args.month==12),args.month%12+1,1)-timedelta(days=1))
    safe=datetime.now(MOSCOW).date()-timedelta(days=1)
    finish=min(finish,safe)
    if finish<start:p.error("no past completed dates")
    if not args.output.is_dir() or args.output.is_symlink():p.error("existing isolated destination required")
    db=args.output/ARCHIVE
    if db.exists() or db.is_symlink():p.error("new isolated DuckDB required")
    svc=MarketDataService.default()
    archive=ResearchIntradayArchive(str(db))
    report=[]
    failures=0
    try:
        day=start
        while day<=finish:
            daystr=day.isoformat()
            try:
                instrument=svc.resolve("BR",as_of=day)
                if instrument.symbol!="BR" or instrument.asset_class!="future" or not instrument.secid.startswith("BR"):
                    raise ValueError("wrong BR exact future contract")
                daily=svc.candles_for_instrument(instrument,timeframe="D1",from_date=daystr,till_date=daystr,completed_only=True)
                hasday=any(c.begin[:10]==daystr and c.completed for c in daily.candles)
                if not hasday:
                    record={"day":daystr,"secid":instrument.secid,"status":"NO_D1_SOURCE_DAY_NOT_ASSUMED_TRADING"}
                    report.append(record)
                    print(json.dumps(record,sort_keys=True),flush=True)
                    day+=timedelta(days=1)
                    continue
                require_historical_intraday_scope("H1",daystr,daystr)
                source=prepare_one_day(svc,instrument,daystr)
                for series in (source.hourly,source.quarter_hour):
                    if series.candles:archive.ingest(series)
                if source.hourly.count and source.quarter_hour.count:
                    archive.mark_captured(instrument,daystr,h1_rows=source.hourly.count,
                       m15_rows=source.quarter_hour.count,raw_m1_rows=source.minute_rows_seen,
                       incomplete_m15_buckets=source.incomplete_m15_buckets,skipped_h1_bars=source.skipped_h1_bars)
                else:failures+=1
                record={"day":daystr,"secid":instrument.secid,"status":"CAPTURED_SOURCE_WITH_GAPS" if (source.incomplete_m15_buckets or source.skipped_h1_bars) else "CAPTURED_SOURCE_STRICT",
                   "h1":source.hourly.count,"m15":source.quarter_hour.count,"m1_transient":source.minute_rows_seen,
                   "m15_incomplete_excluded":source.incomplete_m15_buckets,"h1_short_excluded":source.skipped_h1_bars}
                report.append(record)
                print(json.dumps(record,sort_keys=True),flush=True)
            except Exception as e:
                failures+=1
                rate_limited=(type(e).__name__=="UpstreamRateLimited" or
                              "last_status=503" in str(e) or "last_status=429" in str(e))
                record={"day":daystr,
                        "status":"UPSTREAM_TEMPORARILY_UNAVAILABLE" if rate_limited else "ERROR",
                        "error_type":type(e).__name__,"error":str(e)[:280]}
                report.append(record)
                print(json.dumps(record,sort_keys=True),flush=True)
                # Stop this month on an upstream rate-limit; never retry every
                # remaining calendar day against the same failing endpoint.
                if rate_limited:
                    break
            day+=timedelta(days=1)
    finally:archive.close()
    con=duckdb.connect(str(db),read_only=True)
    try:
        rows=con.execute("""select secid,timeframe,begin,end_time,open,high,low,close,volume,value,source_kind,normalized_sha256
                           from research_intraday_candles where symbol='BR' order by timeframe,secid,begin""").fetchall()
    finally:con.close()
    payload=[];provenance=[];keys=set()
    for secid,tf,begin,end,o,h,l,c,vol,value,kind,fingerprint in rows:
        key=f"BR|{tf}|{secid}|{begin}"
        if key in keys:raise RuntimeError("duplicate source key")
        keys.add(key)
        if not l<=min(o,c)<=max(o,c)<=h:raise RuntimeError("invalid source price")
        payload.append([key,begin[:10],"","",begin,end,"","UNKNOWN",secid,o,h,l,c,vol,"",value,
                        "TRUE","H1" if tf=="H1" else "M1",1 if tf=="H1" else 15,"MOEX_ISS"])
        provenance.append({"record_key":key,"normalized_sha256":fingerprint,"source_kind":kind})
    with (args.output/"verified_sheet_rows.csv").open("w",newline="") as f:csv.writer(f).writerows(payload)
    with (args.output/"source_provenance.json").open("w") as f:json.dump(provenance,f,ensure_ascii=False,separators=(",",":"))
    summary={"symbol":"BR","range":[start.isoformat(),finish.isoformat()],
          "rows":{"H1":sum(x[1]=="H1" for x in rows),"M15":sum(x[1]=="M15" for x in rows)},
          "source_days":sum(x.get("status","").startswith("CAPTURED") for x in report),
          "no_d1_source_days":sum(x["status"]=="NO_D1_SOURCE_DAY_NOT_ASSUMED_TRADING" for x in report),
          "errors":failures,"calendar_attested":False,"pit_attested":False,
          "M1_persisted":False,"research_only":True,"db_sha256":sha(db),"days":report}
    (args.output/"source_quality.json").write_text(json.dumps(summary,sort_keys=True,indent=2,ensure_ascii=False)+"\n")
    (args.output/"SHA256SUMS.txt").write_text("".join(f"{sha(x)}  {x.name}\n" for x in [db,args.output/"verified_sheet_rows.csv",args.output/"source_provenance.json",args.output/"source_quality.json"]))
    print(json.dumps({"status":"PARTIAL_RESEARCH" if failures else "SOURCE_COLLECTED_NOT_CALENDAR_ATTESTED","rows":summary["rows"],"source_days":summary["source_days"],"errors":failures,"db_sha256":summary["db_sha256"]},sort_keys=True),flush=True)
    return 1 if failures else 0
if __name__=="__main__":raise SystemExit(run())
