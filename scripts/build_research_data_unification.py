"""Build an isolated, versioned data research store; never writes HOME or Google.

When HOME backup input is used, run with a private local output directory.
Raw archived server files remain in place; all source tables are read-only views.
"""
from __future__ import annotations
import argparse, collections, dataclasses, datetime as dt, gzip, hashlib, json, math
from pathlib import Path
import duckdb
from birzha.application.features import TimeframeFeatureEngine
from birzha.domain.market import Candle, CandleSeries, Instrument

BASE_SHA="fc2572d174be435647fb6a5ea95a5b1611d3d74a"
FEATURES={
 "return_5":("return_5",6), "return_10":("return_10",11), "return_20":("return_20",21),
 "sma_20":("sma_20",20), "sma_50":("sma_50",50), "efficiency_ratio_20":("efficiency_ratio_20",21),
 "atr_14_pct":("atr_14_pct",15), "volume_ratio_20":("volume_ratio_20",21),
 "vwap_20_candle_approx":("vwap_20",20), "price_location_20":("price_location_20",20),
 "support_20_observed_min":("support_20",20), "resistance_20_observed_max":("resistance_20",20),
 "trend_score_legacy":("trend_score",50)}
def js(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
def sqls(x): return "'"+str(x).replace("'","''")+"'"
def sqlcols(columns): return "{"+",".join(sqls(k)+":"+sqls(v) for k,v in columns.items())+"}"
def sha_file(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def finite(x): return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)

def make_observations(c,audit_dir,home):
    columns={"symbol":"VARCHAR","timeframe":"VARCHAR","source_row":"BIGINT","source_record_sha256":"VARCHAR","source":"JSON","exact_secid":"VARCHAR","parsed_begin":"VARCHAR","parsed_end":"VARCHAR"}
    c.execute("CREATE TABLE raw_google_candles AS SELECT * FROM read_json("+sqls(audit_dir/"candle_records.jsonl.gz")+",format='newline_delimited',columns="+sqlcols(columns)+")")
    # Provider-clock interpretation is explicit; it is never a historical receipt.
    query="""SELECT 'GOOGLE_SNAPSHOT' input_scope,symbol,exact_secid secid,timeframe,
       try_cast(substr(parsed_begin,1,19) AS TIMESTAMP) start_time,
       try_cast(substr(parsed_end,1,19) AS TIMESTAMP) end_time,
       json_extract_string(source,'$.bar_start_time') raw_begin,
       json_extract_string(source,'$.bar_end_time') raw_end,
       try_cast(json_extract_string(source,'$.open') AS DOUBLE) AS "open",
       try_cast(json_extract_string(source,'$.high') AS DOUBLE) AS "high",
       try_cast(json_extract_string(source,'$.low') AS DOUBLE) AS "low",
       try_cast(json_extract_string(source,'$.close') AS DOUBLE) AS "close",
       try_cast(json_extract_string(source,'$.volume') AS DOUBLE) volume,
       try_cast(json_extract_string(source,'$.turnover') AS DOUBLE) turnover,
       try_cast(json_extract_string(source,'$.is_complete') AS BOOLEAN) complete,
       nullif(json_extract_string(source,'$.available_at'),'') available_at,
       json_extract_string(source,'$.available_at_confidence') available_at_confidence,
       nullif(json_extract_string(source,'$.observed_at'),'') observed_at,
       json_extract_string(source,'$.record_key') record_key,
       source_record_sha256 input_row_sha256,
       coalesce(nullif(json_extract_string(source,'$.research_status'),''),'LEGACY_UNVERIFIED') source_quality
       FROM raw_google_candles"""
    if home:
        c.execute("CREATE VIEW home_backup_candles AS SELECT * FROM read_parquet("+sqls(home/"historical_candles.parquet")+")")
        c.execute("CREATE VIEW home_backup_flow AS SELECT * FROM read_parquet("+sqls(home/"historical_flow_rows.parquet")+")")
        c.execute("""CREATE VIEW home_eligible AS SELECT *,
          CASE WHEN secid IN ('SBER','IMOEX','RTSI') THEN secid
          WHEN engine='futures' AND market='forts' AND starts_with(upper(secid),'SI') THEN 'Si'
          WHEN engine='futures' AND market='forts' AND starts_with(upper(secid),'BR') THEN 'BR'
          WHEN engine='futures' AND market='forts' AND starts_with(upper(secid),'GD') THEN 'GOLD'
          ELSE NULL END canonical_symbol FROM home_backup_candles""")
        query+=""" UNION ALL SELECT 'HOME_ACCEPTED_BACKUP',canonical_symbol,secid,timeframe,
          try_cast(substr(replace(begin,'T',' '),1,19) AS TIMESTAMP),
          try_cast(substr(replace(end_time,'T',' '),1,19) AS TIMESTAMP),
          begin,end_time,"open","high","low","close",volume,"value",completed,available_at,
          available_at_confidence,observed_at,secid||'|'||timeframe||'|'||begin,
          sha256(to_json(struct_pack(secid:=secid,tf:=timeframe,bar_begin:=begin,bar_end:=end_time,
             o:="open",h:="high",l:="low",c:="close",v:=volume,t:="value",complete:=completed))),
          'STORED_BACKUP_NOT_HISTORICAL_ATTESTATION'
          FROM home_eligible WHERE canonical_symbol IS NOT NULL AND timeframe IN ('D1','H1','M15')"""
    c.execute("CREATE VIEW observations AS "+query)
    c.execute("""CREATE TABLE input_groups AS SELECT input_scope,symbol,secid,timeframe,start_time,
       count(*) aliases,
       count(DISTINCT sha256(to_json(struct_pack(bar_end:=end_time,o:="open",h:="high",l:="low",c:="close",v:=volume,t:=turnover)))) variants,
       bool_and(coalesce(complete,false)) accepted_complete,
       sha256(string_agg(input_row_sha256,'|' ORDER BY input_row_sha256)) lineage_sha256
       FROM observations GROUP BY 1,2,3,4,5""")
    c.execute("""CREATE VIEW feature_inputs AS SELECT o.*,g.aliases,g.accepted_complete,g.lineage_sha256
      FROM observations o JOIN input_groups g USING(input_scope,symbol,secid,timeframe,start_time)
      WHERE g.variants=1 AND o.start_time IS NOT NULL AND o.end_time>=o.start_time
      QUALIFY row_number() OVER(PARTITION BY input_scope,symbol,secid,timeframe,start_time ORDER BY record_key,input_row_sha256)=1""")

def indicator_records(c,formula_version,calculated):
    engine=TimeframeFeatureEngine(); previous=None; window=[]; lineage=[]; instrument=None
    columns=[x[0] for x in c.execute("SELECT * FROM feature_inputs LIMIT 0").description]
    cursor=c.execute("SELECT * FROM feature_inputs ORDER BY input_scope,symbol,secid,timeframe,start_time")
    while batch:=cursor.fetchmany(1000):
      for values in batch:
        row=dict(zip(columns,values)); group=tuple(row[k] for k in ("input_scope","symbol","secid","timeframe"))
        if group!=previous:
            window=[];lineage=[];previous=group
            instrument=Instrument(symbol=row["symbol"],secid=row["secid"],board="",engine="",market="",asset_class="index" if row["symbol"] in ("IMOEX","RTSI") else "equity" if row["symbol"]=="SBER" else "future")
        valid=all(finite(row[k]) for k in ("open","high","low","close"))
        valid=valid and row["low"]<=min(row["open"],row["high"],row["close"]) and row["high"]>=max(row["open"],row["low"],row["close"])
        eligible=valid and row["accepted_complete"] is True
        reason="SOURCE_INCOMPLETE" if valid else "INVALID_SOURCE_PRICE"
        if eligible:
            window.append(Candle(open=row["open"],high=row["high"],low=row["low"],close=row["close"],volume=row["volume"],value=row["turnover"],begin=row["raw_begin"],end=row["raw_end"],completed=True))
            lineage.append(row["lineage_sha256"])
            window=window[-51:];lineage=lineage[-51:]
        else:
            window=[];lineage=[]
        record=dict(record_key=hashlib.sha256(js([*group,row["record_key"],formula_version]).encode()).hexdigest(),
          input_scope=row["input_scope"],symbol=row["symbol"],secid=row["secid"],timeframe=row["timeframe"],
          source_record_key=row["record_key"],begin=row["start_time"].isoformat(),end=row["end_time"].isoformat(),
          source_record_sha256=row["input_row_sha256"],source_window_count=len(window),
          source_window_sha256=hashlib.sha256(js(lineage).encode()).hexdigest(),
          window_start=window[0].begin if window else None,window_end=window[-1].end if window else None,
          formula_bundle_version=formula_version,calculated_at=calculated,available_at=None,
          available_at_confidence="UNKNOWN",quality_status="RECONSTRUCTED_RESEARCH_ONLY",
          calendar_status="UNVERIFIED",source_complete_flag=row["accepted_complete"])
        state=dataclasses.asdict(engine.build(CandleSeries(instrument,row["timeframe"],tuple(window)))) if window else {}
        statuses={}
        for output,(field,needed) in FEATURES.items():
            value=None
            if not eligible: status=reason
            elif len(window)<needed: status="INSUFFICIENT_HISTORY"
            elif output in ("volume_ratio_20","vwap_20_candle_approx") and row["symbol"] in ("IMOEX","RTSI"): status="NOT_APPLICABLE_INDEX_VOLUME"
            elif output in ("volume_ratio_20","vwap_20_candle_approx") and any(x.volume is None or x.volume<0 for x in window[-(21 if output=="volume_ratio_20" else 20):]): status="MISSING_VOLUME_IN_WINDOW"
            else:
                value=state.get(field); status="RECONSTRUCTED_RESEARCH_ONLY" if value is not None else "UNAVAILABLE_SOURCE_OR_ZERO_DENOMINATOR"
            record[output]=value;statuses[output]=status
        atr=record["atr_14_pct"]
        record["atr_14_sma"]=atr*row["close"] if atr is not None else None
        statuses["atr_14_sma"]=statuses["atr_14_pct"]
        record["indicator_status"]=statuses
        yield record

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--audit",type=Path,required=True);ap.add_argument("--home-backup",type=Path);ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    if args.home_backup and args.output.resolve().drive.lower()!=args.home_backup.resolve().drive.lower():
        raise ValueError("HOME-derived research must remain on the same local volume; external export requires a separate approved operation")
    args.output.mkdir(parents=True,exist_ok=True)
    db=args.output/"research_data_unification.duckdb"
    if db.exists(): raise ValueError("Refusing to replace an existing research store")
    audit=json.loads((args.audit/"audit_summary.json").read_text(encoding="utf-8"))
    immutable={b["symbol"]:sha_file(b["file"])==b["file_sha256"] for b in audit["books"]}
    if not all(immutable.values()):raise ValueError("Input XLSX changed since audit")
    feature_path=Path(__file__).resolve().parents[1]/"src/birzha/application/features.py"
    formula_version="MAIN_FEATURES_"+BASE_SHA+"_"+sha_file(feature_path)+"_COMPLETE_EXACT_CONTRACT_WINDOWS_V1"
    calculated=dt.datetime.now(dt.timezone.utc).isoformat()
    c=duckdb.connect(str(db));make_observations(c,args.audit,args.home_backup)
    summary=dict(schema="DATA_UNIFICATION_RESEARCH_V1",code_base_sha=BASE_SHA,formula_bundle_version=formula_version,calculated_at=calculated,
      database=str(db.resolve()),source_files_unchanged=immutable,strict_forecast_admission="NOT_GRANTED",
      clock_interpretation="PROVIDER_EUROPE_MOSCOW_NOT_HISTORICAL_RECEIPT",calendar_gaps="UNVERIFIED",
      observations=c.execute("SELECT input_scope,symbol,timeframe,count(*) FROM observations GROUP BY 1,2,3 ORDER BY 1,2,3").fetchall(),
      semantic_groups=c.execute("SELECT input_scope,symbol,timeframe,sum(aliases),count(*),sum(CASE WHEN variants>1 THEN 1 ELSE 0 END) FROM input_groups GROUP BY 1,2,3 ORDER BY 1,2,3").fetchall())
    if args.home_backup:
        summary["excluded_instrument_family"]=c.execute("SELECT secid,engine,market,asset_class,timeframe,count(*) FROM home_eligible WHERE canonical_symbol IS NULL GROUP BY 1,2,3,4,5").fetchall()
        summary["source_comparison"]=c.execute("""SELECT coalesce(g.symbol,h.symbol),coalesce(g.timeframe,h.timeframe),
         count(*) FILTER(WHERE g.record_key IS NOT NULL AND h.record_key IS NOT NULL),
         count(*) FILTER(WHERE g.record_key IS NOT NULL AND h.record_key IS NULL),
         count(*) FILTER(WHERE h.record_key IS NOT NULL AND g.record_key IS NULL),
         count(*) FILTER(WHERE g.record_key IS NOT NULL AND h.record_key IS NOT NULL AND
           (g."open" IS DISTINCT FROM h."open" OR g."high" IS DISTINCT FROM h."high" OR g."low" IS DISTINCT FROM h."low" OR g."close" IS DISTINCT FROM h.close)),
         count(*) FILTER(WHERE g.record_key IS NOT NULL AND h.record_key IS NOT NULL AND g.end_time IS DISTINCT FROM h.end_time),
         count(*) FILTER(WHERE g.record_key IS NOT NULL AND h.record_key IS NOT NULL AND g.volume IS DISTINCT FROM h.volume),
         count(*) FILTER(WHERE g.record_key IS NOT NULL AND h.record_key IS NOT NULL AND g.accepted_complete=false AND h.accepted_complete=true)
         FROM (SELECT * FROM feature_inputs WHERE input_scope='GOOGLE_SNAPSHOT') g FULL JOIN
           (SELECT * FROM feature_inputs WHERE input_scope='HOME_ACCEPTED_BACKUP') h
         ON g.symbol=h.symbol AND g.secid=h.secid AND g.timeframe=h.timeframe AND g.start_time=h.start_time
         GROUP BY 1,2 ORDER BY 1,2""").fetchall()
    path=args.output/"indicators.jsonl.gz";digest=hashlib.sha256();counts=collections.Counter();available=collections.Counter()
    with gzip.open(path,"wt",encoding="utf-8") as f:
        for record in indicator_records(c,formula_version,calculated):
            f.write(js(record)+"\n")
            deterministic={k:v for k,v in record.items() if k!="calculated_at"}
            digest.update((js(deterministic)+"\n").encode())
            counts[(record["input_scope"],record["symbol"],record["timeframe"])]+=1
            for name in [*FEATURES,"atr_14_sma"]:
                if record[name] is not None: available[(record["input_scope"],record["symbol"],record["timeframe"],name)]+=1
            if sum(counts.values())%50000==0: print("INDICATORS",sum(counts.values()),flush=True)
    columns={k:"VARCHAR" for k in ("record_key","input_scope","symbol","secid","timeframe","source_record_key","begin","end","source_record_sha256","source_window_sha256","window_start","window_end","formula_bundle_version","calculated_at","available_at","available_at_confidence","quality_status","calendar_status")}
    columns.update(source_window_count="BIGINT",source_complete_flag="BOOLEAN",indicator_status="JSON")
    columns.update({k:"DOUBLE" for k in [*FEATURES,"atr_14_sma"]})
    c.execute("CREATE TABLE indicators AS SELECT * FROM read_json("+sqls(path)+",format='newline_delimited',columns="+sqlcols(columns)+")")
    summary["indicator_records"]=[[*k,n] for k,n in sorted(counts.items())]
    summary["available_indicator_values"]=[[*k,n] for k,n in sorted(available.items())]
    summary["indicator_values_sha256_excluding_calculation_time"]=digest.hexdigest()
    summary["indicator_rows_readback"]=c.execute("SELECT count(*),count(DISTINCT record_key) FROM indicators").fetchone()
    summary["pilot_SBER"]=dict(source_rows=sum(b["sheets"][tf]["candles"]["records"] for b in audit["books"] if b["symbol"]=="SBER" for tf in ("D1","H1","M15")),
      staged_rows=c.execute("SELECT count(*) FROM feature_inputs WHERE input_scope='GOOGLE_SNAPSHOT' AND symbol='SBER'").fetchone()[0],
      indicator_rows=c.execute("SELECT count(*) FROM indicators WHERE input_scope='GOOGLE_SNAPSHOT' AND symbol='SBER'").fetchone()[0],
      source_ohlcv_mutations=c.execute("""SELECT count(*) FROM raw_google_candles r LEFT JOIN feature_inputs f ON f.input_scope='GOOGLE_SNAPSHOT' AND r.symbol=f.symbol AND r.timeframe=f.timeframe AND r.source_record_sha256=f.input_row_sha256 WHERE r.symbol='SBER' AND (f.record_key IS NULL OR f."open" IS DISTINCT FROM try_cast(json_extract_string(r.source,'$.open') AS DOUBLE) OR f."high" IS DISTINCT FROM try_cast(json_extract_string(r.source,'$.high') AS DOUBLE) OR f."low" IS DISTINCT FROM try_cast(json_extract_string(r.source,'$.low') AS DOUBLE) OR f."close" IS DISTINCT FROM try_cast(json_extract_string(r.source,'$.close') AS DOUBLE) OR f.volume IS DISTINCT FROM try_cast(json_extract_string(r.source,'$.volume') AS DOUBLE) OR f.raw_begin IS DISTINCT FROM json_extract_string(r.source,'$.bar_start_time') OR f.raw_end IS DISTINCT FROM json_extract_string(r.source,'$.bar_end_time'))""").fetchone()[0],
      original_keys_preserved=c.execute("""SELECT count(*) FROM raw_google_candles r LEFT JOIN feature_inputs f ON f.input_scope='GOOGLE_SNAPSHOT' AND r.symbol=f.symbol AND r.timeframe=f.timeframe AND json_extract_string(r.source,'$.record_key')=f.record_key WHERE r.symbol='SBER' AND f.record_key IS NULL""").fetchone()[0]==0,physical_google_schema_changed=False)
    summary["unimplemented_or_unapproved_variants"]=["WILDER_ATR14_OPEN_PR254","MEDIAN_VOLUME20_OPEN_PR254","CONFIRMED_HH_HL_OPEN_PR254","REALIZED_VOLATILITY_FORMULA_NOT_ADMITTED"]
    summary["source_files_unchanged_after"]={b["symbol"]:sha_file(b["file"])==b["file_sha256"] for b in audit["books"]}
    c.execute("CHECKPOINT");c.close()
    (args.output/"research_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(js({"database":str(db),"indicator_rows":sum(counts.values()),"sha256":digest.hexdigest(),"pilot":summary["pilot_SBER"]}),flush=True)

if __name__=="__main__":main()
