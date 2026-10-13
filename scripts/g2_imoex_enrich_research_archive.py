"""Upgrade a COPY of IMOEX 2025-26 DuckDB with native-source exceptions.

Original canonical SHA is verified then read-only/copy. Recovered observed H1
and M15 with partial minute coverage are a separate table. They do not
masquerade as complete strict candles, nor as synthetic filled prices.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import shutil

import duckdb

from scripts.g2_imoex_source_interval_recovery import SOURCE_SHA, sha256
from scripts.g2_imoex_merge_research_months import expected_months

QUALITY={"H1":"NATIVE_H1_SHORT_OBSERVED",
         "M15":"SOURCE_M15_WITH_FEWER_THAN_15_M1"}
TABLE="research_intraday_source_observed_exceptions"


def _validate_report(directory:Path,month:str):
    file=directory/f"IMOEX_{month}_AUTHENTIC_SOURCE_EXCEPTIONS.json"
    sums=directory/f"IMOEX_{month}_RECOVERY_SHA256SUMS.txt"
    if any(not p.is_file() or p.is_symlink() for p in (file,sums)):
        raise ValueError(f"missing recovery report and sha {month}")
    checksum=sums.read_text().strip().split()
    if len(checksum)!=2 or checksum[1]!=file.name or checksum[0]!=sha256(file):
        raise ValueError(f"tampered recovery report {month}")
    report=json.loads(file.read_text())
    if (report.get("schema")!="IMOEX_SOURCE_INTERVAL_RECOVERY_V1" or
        report.get("month")!=month or report.get("original_archive_sha256")!=SOURCE_SHA or
        report.get("source_days_match_original") is not True or
        report.get("source_changes_rejected") is not True or
        report.get("no_invented_prices") is not True):
        raise ValueError(f"invalid source recovery attestations {month}")
    records=report.get("rows")
    if not isinstance(records,list) or len(records)!=report.get("days") or not records:
        raise ValueError(f"empty/wrong day recovery evidence {month}")
    seen=set()
    rec_h1=rec_m15=un_h1=un_m15=0
    extracted=[]
    for day in records:
        day_id=day.get("day")
        if (not isinstance(day_id,str) or not day_id.startswith(month) or
            day_id in seen or len(day_id)!=10):
            raise ValueError(f"duplicate/out-of-month source day {month}")
        seen.add(day_id)
        exceptions=day.get("supplemental")
        if not isinstance(exceptions,list):
            raise ValueError(f"missing detailed source rows {day_id}")
        h1=sum(x.get("timeframe")=="H1" for x in exceptions)
        m15=sum(x.get("timeframe")=="M15" for x in exceptions)
        if h1!=day["recovered_h1"] or m15!=day["recovered_m15"]:
            raise ValueError(f"source exception counts differ {day_id}")
        for row in exceptions:
            tf=row["timeframe"]
            if tf not in QUALITY or row["quality"]!=QUALITY[tf] or row.get("synthetic_price") is not False:
                raise ValueError(f"source exception quality/anti-fabrication rejected {day_id}")
            if row.get("secid")!="IMOEX" or row.get("symbol")!="IMOEX":
                raise ValueError("wrong source index")
            begin=datetime.fromisoformat(row["begin"])
            end=datetime.fromisoformat(row["end"])
            if begin.tzinfo is None or end.tzinfo is None or begin.date().isoformat()!=day_id:
                raise ValueError(f"no zoned session boundary {day_id}")
            seconds=(end-begin).total_seconds()
            if tf=="H1":
                if (row.get("source_minute_rows") is not None or not 0<seconds<=3600
                    or (seconds in (3599,3600) and begin.minute==0 and begin.second==0)):
                    raise ValueError(f"H1 must reflect real noncanonical native interval {day_id}")
            else:
                count=row.get("source_minute_rows")
                if (type(count) is not int or not 1<=count<15
                        or begin.minute%15 or begin.second or seconds not in (899,900)):
                    raise ValueError(f"M15 requires authentic partial 1..14 minute rows {day_id}")
            o,h,l,c=(row[k] for k in ("open","high","low","close"))
            if any(type(v) not in (float,int) or not math.isfinite(v) or v<=0 for v in (o,h,l,c)):
                raise ValueError(f"nonfinite price {day_id}")
            if not l<=min(o,c)<=max(o,c)<=h:
                raise ValueError(f"bad source OHLC {day_id}")
            for field in ("volume","value"):
                value=row.get(field)
                if value is not None and (type(value) not in (float,int) or
                                           not math.isfinite(value) or value<0):
                    raise ValueError(f"invalid {field} {day_id}")
            extracted.append((row["symbol"],row["secid"],tf,row["begin"],row["end"],
                              day_id,o,h,l,c,row.get("volume"),row.get("value"),
                              row["quality"],row["source_minute_rows"],
                              "OBSERVED_MOEX_H1" if tf=="H1" else "MOEX_M1_V1_COMPAT",
                              row["available_at_confidence"]))
        rec_h1+=h1;rec_m15+=m15
        un_h1+=day["unresolved_h1_reported"]
        un_m15+=day["unresolved_m15_reported"]
    if (rec_h1!=report["native_short_h1"] or rec_m15!=report["partial_m15_from_source"]
        or un_h1!=report["unresolved_h1_reported"] or
        un_m15!=report["unresolved_m15_reported"]):
        raise ValueError(f"recovery aggregate mismatch {month}")
    return seen,extracted,(rec_h1,rec_m15,un_h1,un_m15)


def upgrade(original:Path,recovery_dir:Path,out:Path,summary_path:Path):
    if (out.exists() or out.is_symlink() or summary_path.exists() or
        not out.parent.is_dir() or not summary_path.parent.is_dir() or
        out==original or not out.name.endswith(".duckdb") or
        summary_path.suffix!=".json"):
        raise ValueError("must create separate clean V2 research outputs")
    if not original.is_file() or original.is_symlink() or sha256(original)!=SOURCE_SHA:
        raise ValueError("original canonical research archive altered")
    months=expected_months()
    reports={x.name.removeprefix("imoex-source-exceptions-"):x
             for x in recovery_dir.glob("imoex-source-exceptions-????-??")
             if x.is_dir() and not x.is_symlink()}
    if set(reports)!=set(months):
        raise ValueError(f"must have exact 22 verified monthly exception reports: {sorted(reports)}")
    allrows=[];evidence_days=set();counts=[0,0,0,0]
    for month in months:
        days,rows,totals=_validate_report(reports[month],month)
        if evidence_days.intersection(days):
            raise ValueError("duplicate recovery source day")
        evidence_days.update(days)
        allrows.extend(rows)
        counts=[a+b for a,b in zip(counts,totals)]
    if (len(evidence_days)!=457 or counts[2:] != [0,0] or
        tuple(counts[:2])!=(541,1296) or len(allrows)!=(541+1296)):
        raise ValueError(f"not all authentic historical source exceptions recovered {counts}")
    original_conn=duckdb.connect(str(original),read_only=True)
    try:
        cp=original_conn.execute("""
          SELECT trade_date,incomplete_m15_buckets,skipped_h1_bars
          FROM research_intraday_captured_days
        """).fetchall()
        if (set(d for d,*_ in cp)!=evidence_days
            or sum(x[1] for x in cp)!=counts[1]
            or sum(x[2] for x in cp)!=counts[0]):
            raise ValueError("recovered source dates do not correspond to canonical checkpoints")
        existing=original_conn.execute("""
          SELECT timeframe,begin FROM research_intraday_candles
          WHERE secid='IMOEX'
        """).fetchall()
        seen=set(existing)
        for r in allrows:
            key=(r[2],r[3])
            if key in seen:
                raise ValueError(f"duplicate existing/supplemental candle: {key}")
            seen.add(key)
    finally:
        original_conn.close()
    shutil.copy2(original,out)
    try:
        cx=duckdb.connect(str(out))
        try:
            cx.execute("BEGIN TRANSACTION")
            cx.execute("""
                CREATE TABLE research_intraday_source_observed_exceptions(
                    symbol VARCHAR NOT NULL,secid VARCHAR NOT NULL,timeframe VARCHAR NOT NULL,
                    begin VARCHAR NOT NULL,end_time VARCHAR NOT NULL,trade_date VARCHAR NOT NULL,
                    open DOUBLE NOT NULL,high DOUBLE NOT NULL,low DOUBLE NOT NULL,close DOUBLE NOT NULL,
                    volume DOUBLE,value DOUBLE,quality VARCHAR NOT NULL,source_minute_rows INTEGER,
                    source_kind VARCHAR NOT NULL,available_at_confidence VARCHAR NOT NULL,
                    PRIMARY KEY(secid,timeframe,begin))
            """)
            cx.executemany("INSERT INTO research_intraday_source_observed_exceptions VALUES ("+
                           ",".join("?" for _ in allrows[0])+")",allrows)
            cx.execute("""
                INSERT INTO research_intraday_meta VALUES
                ('source_compatible_exception_extension','MOEX_SOURCE_OBSERVED_V1')
            """)
            cx.execute("COMMIT")
        except Exception:
            cx.execute("ROLLBACK")
            raise
        finally:
            cx.close()
    except Exception:
        out.unlink(missing_ok=True)
        raise
    report={
        "schema":"IMOEX_RESEARCH_CANONICAL_PLUS_OBSERVED_PARTIAL_SOURCE_V2",
        "original_v1_db_sha256":SOURCE_SHA,
        "new_v2_db_sha256":sha256(out),
        "source_dates":len(evidence_days),
        "canonical_h1":4199,"canonical_m15":16073,
        "source_native_noncanonical_h1":counts[0],
        "source_partial_m15_1_to_14_minutes":counts[1],
        "total_observed_h1":4199+counts[0],
        "total_observed_m15":16073+counts[1],
        "unresolved_excluded_from_original":0,
        "minutes_M1_persisted":False,
        "original_canonical_untouched":True,
        "source_partial_ohlcv_observations_reproduced":True,
        "future_prediction_pit_attested":False,
        "full_session_completeness_attested":False,
        "live_forecast_v1_compat_source_not_trading_proven":True,
        "no_trade":True,"release_blocked":True,
    }
    summary_path.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--original",type=Path,required=True)
    p.add_argument("--recovery-root",type=Path,required=True)
    p.add_argument("--target",type=Path,required=True)
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    result=upgrade(args.original,args.recovery_root,args.target,args.report)
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
