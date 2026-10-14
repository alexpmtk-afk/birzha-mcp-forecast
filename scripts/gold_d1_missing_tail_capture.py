"""Isolated MOEX GOLD D1 missing-tail capture, never writes canonical Sheets.

Conservatively collect 2026-09-16..2026-10-13 for every exact historical
GD quarterly SECID listed by MOEX futures history. This research receipt
does not assert that all older GD contracts are covered or PIT-ready.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import traceback
from zoneinfo import ZoneInfo

import duckdb

from birzha.application.market_data import MarketDataService
from birzha.providers.moex_history import _pick_instrument

FIRST, LAST = date(2026,9,16), date(2026,10,13)
SECID = re.compile(r"^GD[HMUZ]\d{1,2}$")
MOSCOW = ZoneInfo("Europe/Moscow")
NAME = "gold_d1_missing_tail_20260916_20261013.duckdb"
HEADERS = (
    "record_key","calendar_date","trade_session_date","session_id",
    "bar_start_time","bar_end_time","available_at","available_at_confidence",
    "contract_code","open","high","low","close","volume",
    "number_of_trades","turnover","is_complete","source_interval",
    "source_candles","source_id","research_status","normalized_sha256",
    "source_archive_sha256","capture_day_status","source_kind","caveat",
)

def digest(p: Path) -> str:
    d=hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): d.update(block)
    return d.hexdigest()

def listed_contracts(service, day):
    payload=service.provider._request(
        "/history/engines/futures/markets/forts/securities.json",
        {"iss.meta":"off","iss.only":"history",
         "history.columns":("TRADEDATE,SECID,BOARDID,ASSETCODE,VALUE,VOLUME,"
                            "OPENPOSITIONVALUE,OPENPOSITION,SHORTNAME,"
                            "LASTTRADEDATE,LASTDELDATE,MINSTEP,STEPPRICE,LOTVOLUME,CURRENCYID"),
         "date":day.isoformat(),"assetcode":"GOLD"}).json()
    rows=service.provider._table(payload,"history")
    by_secid={}
    for row in rows:
        key=str(row.get("SECID") or "")
        if (SECID.fullmatch(key) and str(row.get("ASSETCODE") or "").upper()=="GOLD"
            and str(row.get("TRADEDATE") or "")[:10]==day.isoformat()
            and str(row.get("BOARDID") or "RFUD")=="RFUD"):
            by_secid[key]=_pick_instrument("GOLD",day,[row])
    return list(by_secid.values())

def main():
    dest=Path("research/GOLD/D1-tail")
    dest.mkdir(parents=True,exist_ok=True)
    path=dest/NAME
    db=duckdb.connect(str(path))
    db.execute("""CREATE TABLE IF NOT EXISTS d1_evidence(
        secid VARCHAR NOT NULL,begin VARCHAR NOT NULL,end_time VARCHAR NOT NULL,
        source_trade_date VARCHAR NOT NULL,engine VARCHAR,market VARCHAR,board VARCHAR,
        open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,value DOUBLE,
        normalized_sha256 VARCHAR,PRIMARY KEY(secid,begin))""")
    db.execute("""CREATE TABLE IF NOT EXISTS source_days(
        secid VARCHAR,trade_date VARCHAR,observation_status VARCHAR,
        rows_seen INTEGER,notes VARCHAR,PRIMARY KEY(secid,trade_date))""")
    service=MarketDataService.default()
    errors=[]
    receipts=[]
    cursor=FIRST
    while cursor<=LAST:
        d=cursor
        cursor=date.fromordinal(d.toordinal()+1)
        if d.weekday()>=5: continue
        item={"date":d.isoformat(),"contracts":[]}
        try:
            contracts=listed_contracts(service,d)
            item["listed_secids"]=[i.secid for i in contracts]
            for inst in contracts:
                entry={"secid":inst.secid}
                try:
                    series=service.candles_for_instrument(
                        inst,timeframe="D1",from_date=d.isoformat(),
                        till_date=d.isoformat(),completed_only=True)
                    entry["source_rows"]=len(series.candles)
                    inserted=0
                    for bar in series.candles:
                        begin=datetime.fromisoformat(bar.begin)
                        end=datetime.fromisoformat(bar.end)
                        if begin.tzinfo is None: begin=begin.replace(tzinfo=MOSCOW)
                        if end.tzinfo is None: end=end.replace(tzinfo=MOSCOW)
                        begin=begin.astimezone(MOSCOW)
                        end=end.astimezone(MOSCOW)
                        if begin.date()!=d:
                            entry.setdefault("excluded_other_start_date",0)
                            entry["excluded_other_start_date"]+=1
                            continue
                        o,h,l,c=bar.open,bar.high,bar.low,bar.close
                        if any(not isinstance(x,(int,float)) or x<=0 for x in (o,h,l,c)) or not l<=min(o,c)<=max(o,c)<=h:
                            raise ValueError("bad native OHLC")
                        if bar.volume is not None and bar.volume<0:
                            raise ValueError("negative volume")
                        native={"secid":inst.secid,"begin":begin.isoformat(),"end":end.isoformat(),
                                "source_trade_date":d.isoformat(),"open":o,"high":h,"low":l,
                                "close":c,"volume":bar.volume,"value":bar.value}
                        fingerprint=hashlib.sha256(json.dumps(
                            native,sort_keys=True,allow_nan=False).encode()).hexdigest()
                        db.execute("""INSERT OR IGNORE INTO d1_evidence
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",[
                            inst.secid,begin.isoformat(),end.isoformat(),d.isoformat(),
                            inst.engine,inst.market,inst.board,o,h,l,c,bar.volume,bar.value,fingerprint])
                        inserted+=1
                    entry["accepted_rows"]=inserted
                    entry["status"]="SOURCE_RECEIVED" if inserted else "NO_D1_CANDLE"
                    db.execute("INSERT OR REPLACE INTO source_days VALUES (?,?,?,?,?)",[
                        inst.secid,d.isoformat(),entry["status"],inserted,
                        "Native MOEX H24; session calendar not independently verified"])
                except Exception as exc:
                    entry["status"]="ERROR"
                    entry["error"]=type(exc).__name__+": "+str(exc)[:250]
                    errors.append(entry["error"])
                item["contracts"].append(entry)
        except Exception as exc:
            item["listing_error"]=type(exc).__name__+": "+str(exc)[:250]
            errors.append(item["listing_error"])
        receipts.append(item)
        print(json.dumps(item,ensure_ascii=False,sort_keys=True),flush=True)
        with (dest/"source_receipts.jsonl").open("a",encoding="utf-8") as f:
            f.write(json.dumps(item,ensure_ascii=False,sort_keys=True)+"\n")
    db.close()
    # Open the actual sealed DuckDB read-only; never trust just write totals.
    con=duckdb.connect(str(path),read_only=True)
    try:
        rows=con.execute("""SELECT secid,begin,end_time,source_trade_date,engine,market,board,
            open,high,low,close,volume,value,normalized_sha256
            FROM d1_evidence ORDER BY secid,begin""").fetchall()
        checkpoints=con.execute("SELECT count(*) FROM source_days").fetchone()[0]
    finally: con.close()
    sha=digest(path)
    manifest={"tool":"GOLD","scope":"2026-09-16..2026-10-13",
              "research_only":True,"PIT":"NOT_VERIFIED",
              "official_calendar":"NOT_VERIFIED","source":"MOEX_ISS",
              "sha256":sha,"rows":len(rows),"secids":sorted({r[0] for r in rows}),
              "checkpoints":checkpoints,"errors":errors,"bytes":path.stat().st_size,
              "status":"SOURCE_PARTIAL" if errors else "SOURCE_RECEIPT_NOT_PIT_VERIFIED"}
    with (dest/"D1_candidates.csv").open("w",newline="",encoding="utf-8") as out:
        w=csv.writer(out);w.writerow(HEADERS)
        for r in rows:
            secid,begin,end,day,engine,market,board,o,h,l,c,vol,value,fp=r
            if (engine,market,board)!=("futures","forts","RFUD") or not l<=min(o,c)<=max(o,c)<=h:
                errors.append(f"readback mismatch {secid} {begin}")
            # Legacy D1 unchanged; trade-session fields remain unknown until audited.
            w.writerow([
                f"GOLD|D1|{secid}|{begin}",day,"","",begin,end,"","UNKNOWN",
                secid,o,h,l,c,vol,"","",True,"D1",1,"MOEX_ISS",
                "D1_RESEARCH_CANDIDATE_NOT_PIT_VERIFIED",fp,sha,
                "SOURCE_RECEIPT","MOEX_NATIVE_D1",
                "New D1 candidate; verify against canonical keys before append",
            ])
    manifest["csv_sha256"]=digest(dest/"D1_candidates.csv")
    if not rows or errors: manifest["status"]="PARTIAL_OR_INVALID"
    (dest/"manifest.json").write_text(
        json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print("FINAL_D1",json.dumps(manifest,ensure_ascii=False,sort_keys=True),flush=True)
    return 0 if rows and not errors else 2

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        p=Path("research/GOLD/D1-tail")
        p.mkdir(parents=True,exist_ok=True)
        (p/"fatal.log").write_text(traceback.format_exc()[-8000:],encoding="utf-8")
        print("FATAL",repr(exc),flush=True)
        raise
