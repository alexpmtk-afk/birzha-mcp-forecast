"""Consolidate archived flow in the private research database, never in HOME."""
from __future__ import annotations
import argparse,collections,datetime as dt,gzip,hashlib,json,math
from pathlib import Path
import duckdb

def packed(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False,default=str)
def num(x):return float(x) if isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) else None
def stamp(x):
    if x in (None,''):return None
    if isinstance(x,(int,float)):return (dt.datetime(1899,12,30)+dt.timedelta(seconds=round(x*86400))).isoformat()
    return str(x)
def symbol(code):
    if code in ('SBER','GOLD'):return code
    if code.upper().startswith('SI'):return 'Si'
    if code.startswith('BR'):return 'BR'
    if code.startswith('GD'):return 'GOLD'
    return None

def delta_values(b,s,u):
    b,s,u=map(num,(b,s,u))
    if b is None or s is None or b<0 or s<0 or u is not None and u<0:return None,None
    delta=b-s;total=b+s+(u or 0)
    return delta,delta/total if total else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--database',type=Path,required=True);ap.add_argument('--audit',type=Path,required=True);args=ap.parse_args()
    if args.database.resolve().drive.lower()!='c:':raise ValueError('Keep HOME-derived results on the private local volume')
    c=duckdb.connect(str(args.database));existing={r[0] for r in c.execute('SHOW TABLES').fetchall()}
    if 'flow_normalized' in existing:raise ValueError('Existing consolidation must not be replaced')
    archive=[json.loads(s) for s in gzip.open(args.audit/'additional_records.jsonl.gz','rt',encoding='utf-8')]
    live_meta={r['values'][0]:r['values'][1] for r in archive if r['symbol']=='Si' and r['sheet']=='ARCHIVE_LIVE_META' and len(r['values'])>1}
    output=[];issues=[]
    def emit(kind,scope,key,raw,**fields):
        source_sha=hashlib.sha256(packed(raw).encode()).hexdigest()
        record=dict(record_key=hashlib.sha256(packed([kind,scope,fields.get("secid"),fields.get("underlying"),key]).encode()).hexdigest(),kind=kind,input_scope=scope,source_record_key=str(key),source_record_sha256=source_sha,raw=raw,
          symbol=None,secid=None,underlying=None,scope_type=None,event_time=None,logical_begin=None,trade_session_date=None,observed_at=None,first_seen_at=None,publication_time=None,
          available_at=None,available_at_confidence='UNKNOWN',quality='RECONSTRUCTED_PARTIAL_NOT_PIT',formula_version='FLOW_CONSOLIDATION_V1_CAPTURED_SUBSET',
          buy_volume=None,sell_volume=None,unknown_volume=None,total_volume=None,delta=None,delta_ratio=None,cumulative_delta=None,reset_rule=None,unit='SOURCE_NATIVE_UNIT_UNVERIFIED',oi=None,clgroup=None,pos_long=None,pos_short=None,pos_net=None)
        record.update(fields);output.append(record)
    for row in archive:
        sheet=row['sheet'];raw=dict(zip(row['header'],row['values']));scope='GOOGLE_SNAPSHOT';key=f"{row['symbol']}|{sheet}|{row['source_row']}"
        if sheet=='FUTOI' and raw.get('ticker'):
            emit('POSITIONS_FUTOI',scope,key,raw,symbol=row['symbol'],underlying=raw['ticker'],scope_type='UNDERLYING_ALL_CONTRACTS',event_time=f"{raw['tradedate']}T{raw['tradetime']}",trade_session_date=raw.get('trade_session_date'),publication_time=raw.get('systime'),clgroup=raw.get('clgroup'),pos_long=num(raw.get('pos_long')),pos_short=num(raw.get('pos_short')),pos_net=num(raw.get('pos')))
        elif sheet in ('ARCHIVE_LIVE_DELTA_M15','ARCHIVE_HISTORY_DELTA_M15'):
            b,s,u=raw.get('buy_quantity'),raw.get('sell_quantity'),raw.get('unknown_quantity');delta,ratio=delta_values(b,s,u)
            if delta!=num(raw.get('calculated_delta')):issues.append(dict(key=key,kind='DELTA_ARITHMETIC_MISMATCH'))
            code=raw.get('security') or live_meta.get('security')
            emit('FLOW_ARCHIVED_M15',scope,key,raw,symbol='Si',secid=code,scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',event_time=stamp(raw.get('calendar_begin')),logical_begin=stamp(raw.get('begin')),trade_session_date=stamp(raw.get('trade_session_date')),first_seen_at=stamp(raw.get('first_seen_at_msk')),buy_volume=num(b),sell_volume=num(s),unknown_volume=num(u),total_volume=num(raw.get('total_quantity')),delta=delta,delta_ratio=ratio,cumulative_delta=num(raw.get('cumulative_delta')),reset_rule='ARCHIVED_PROVIDER_RULE_NOT_REATTESTED',quality='ARCHIVED_PARTIAL_LOGICAL_TIME_NEEDS_MAPPING' if not raw.get('calendar_begin') else 'ARCHIVED_PARTIAL_CALENDAR_BEGIN_PRESENT')
        elif sheet=='ARCHIVE_SNAPSHOT_LOG':
            emit('POSITIONS_SNAPSHOT_OI',scope,key,raw,symbol='Si',secid=raw.get('security'),scope_type='EXACT_CONTRACT_SNAPSHOT',event_time=raw.get('generated_at_utc'),trade_session_date=stamp(raw.get('trade_session_date')),oi=num(raw.get('contract_open_position')),publication_time=raw.get('generated_at_utc'),quality='ARCHIVED_SNAPSHOT_PARTIAL_SESSION')
            emit('PROFILE_SNAPSHOT_SUMMARY',scope,key,raw,symbol='Si',secid=raw.get('security'),scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',event_time=raw.get('generated_at_utc'),trade_session_date=stamp(raw.get('trade_session_date')),quality='ARCHIVED_TRADE_PROFILE_RAW_TRADES_NOT_REATTESTED')
        elif sheet in ('ARCHIVE_LIVE_PROFILE','ARCHIVE_LIVE_NODES'):
            emit('PROFILE_ARCHIVED_BINS' if sheet.endswith('PROFILE') else 'PROFILE_ARCHIVED_NODES',scope,key,raw,symbol='Si',secid=live_meta.get('security'),scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',quality='ARCHIVED_TRADE_PROFILE_RAW_TRADES_NOT_REATTESTED')
    home=c.execute('SELECT dataset,key_symbol,row_key,trade_date,payload_json,source,available_at,available_at_confidence,observed_at FROM home_backup_flow ORDER BY dataset,key_symbol,trade_date,row_key').fetchall()
    raw_trades=collections.defaultdict(list)
    for dataset,code,key,day,payload,source,available,confidence,observed in home:
        raw=json.loads(payload);sym=symbol(code)
        if dataset=='FUTOI':
            emit('POSITIONS_FUTOI','HOME_ACCEPTED_BACKUP',key,raw,symbol=sym,underlying=raw.get('ticker') or code,scope_type='UNDERLYING_ALL_CONTRACTS',event_time=f"{raw.get('tradedate')}T{raw.get('tradetime')}",trade_session_date=raw.get('trade_session_date'),publication_time=raw.get('systime'),observed_at=observed,available_at=available,available_at_confidence=confidence,clgroup=raw.get('clgroup'),pos_long=num(raw.get('pos_long')),pos_short=num(raw.get('pos_short')),pos_net=num(raw.get('pos')))
        elif dataset=='TRADESTATS':
            b,s=raw.get('vol_b'),raw.get('vol_s');delta,ratio=delta_values(b,s,0)
            emit('FLOW_PUBLIC_TRADES_DERIVED_M5','HOME_ACCEPTED_BACKUP',key,raw,symbol=sym,secid=code,scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',event_time=f"{raw.get('tradedate')}T{raw.get('tradetime')}",observed_at=observed,available_at=available,available_at_confidence=confidence,buy_volume=num(b),sell_volume=num(s),unknown_volume=None,total_volume=None if b is None or s is None else b+s,delta=delta,delta_ratio=ratio,reset_rule='NO_SESSION_CUMULATIVE_DELTA_FROM_PARTIAL_AGGREGATES',quality='PUBLIC_TRADES_DERIVED_NOT_SUBSCRIPTION_TRADESTATS')
        elif dataset=='PUBLIC_TRADES_RAW':
            emit('PUBLIC_TRADES_RAW','HOME_ACCEPTED_BACKUP',key,raw,symbol=sym,secid=code,scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',event_time=f"{raw.get('TRADEDATE')}T{raw.get('TRADETIME')}",observed_at=observed,available_at=available,available_at_confidence=confidence,oi=num(raw.get('OPENPOSITION')))
            raw_trades[(code,str(raw.get('TRADEDATE')))].append((key,raw))
    for (code,day),trades in sorted(raw_trades.items()):
        # Reproduce captured-subset arithmetic only. Never label it a complete session.
        bins=collections.defaultdict(list)
        for key,raw in trades:
            if raw.get('OFFMARKETDEAL') not in (None,0):continue
            q=num(raw.get('QUANTITY'));price=num(raw.get('PRICE'))
            if q is None or q<0 or price is None:continue
            bins[price].append((key,raw,q))
        ordered=sorted((x for group in bins.values() for x in group),key=lambda x:(str(x[1].get('TRADETIME')),x[1].get('RECNO') or 0,str(x[1].get('TRADENO'))))
        b=sum(q for _,r,q in ordered if r.get('BUYSELL')=='B');s=sum(q for _,r,q in ordered if r.get('BUYSELL')=='S');u=sum(q for _,r,q in ordered if r.get('BUYSELL') not in ('B','S'));total=b+s+u
        delta,ratio=delta_values(b,s,u)
        emit('FLOW_CAPTURED_DAY_TOTAL','HOME_ACCEPTED_BACKUP',code+'|'+day,{'source_keys':[x[0] for x in ordered]},symbol=symbol(code),secid=code,scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',event_time=day,buy_volume=b,sell_volume=s,unknown_volume=u,total_volume=total,delta=delta,delta_ratio=ratio,cumulative_delta=delta,reset_rule='RESET_EACH_CAPTURED_CALENDAR_DAY_NOT_SESSION',quality='CAPTURED_SUBSET_NO_FULL_DAY_COMPLETENESS')
        for price,group in sorted(bins.items()):
            emit('PROFILE_CAPTURED_EXACT_PRICE_BIN','HOME_ACCEPTED_BACKUP',f'{code}|{day}|{price}',{'price':price,'quantity':sum(q for _,_,q in group),'source_keys':[key for key,_,_ in group]},symbol=symbol(code),secid=code,scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',event_time=day,total_volume=sum(q for _,_,q in group),quality='EXACT_OBSERVED_PRICE_BIN_PARTIAL_SESSION')
        weighted=sum(price*sum(q for _,_,q in group) for price,group in bins.items())
        emit('VWAP_CAPTURED_DAY','HOME_ACCEPTED_BACKUP',code+'|'+day,{'vwap':weighted/total if total else None,'source_keys':[x[0] for x in ordered]},symbol=symbol(code),secid=code,scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',event_time=day,total_volume=total,quality='EXACT_CAPTURED_TRADE_VWAP_NOT_FULL_SESSION')
    path=args.database.parent/'flow_normalized.jsonl.gz';digest=hashlib.sha256()
    with gzip.open(path,'wt',encoding='utf-8') as f:
        for r in sorted(output,key=lambda r:r['record_key']):
            line=packed(r)+'\n';f.write(line);digest.update(line.encode())
    p="'"+str(path).replace("'","''")+"'"
    columns={k:'VARCHAR' for k in output[0]}
    columns.update({k:'DOUBLE' for k in ('buy_volume','sell_volume','unknown_volume','total_volume','delta','delta_ratio','cumulative_delta','oi','pos_long','pos_short','pos_net')})
    columns['raw']='JSON'
    schema='{'+','.join("'"+k+"':'"+v+"'" for k,v in columns.items())+'}'
    c.execute('CREATE TABLE flow_normalized AS SELECT * FROM read_json('+p+",format='newline_delimited',columns="+schema+",maximum_object_size=10000000)")
    rows,unique=c.execute('SELECT count(*),count(DISTINCT record_key) FROM flow_normalized').fetchone()
    if rows!=unique:raise ValueError('Duplicate consolidation keys')
    summary=dict(schema='FLOW_CONSOLIDATION_V1',rows=rows,unique_keys=unique,sha256=digest.hexdigest(),issues=issues,groups=c.execute('SELECT input_scope,symbol,kind,count(*),min(event_time),max(event_time) FROM flow_normalized GROUP BY 1,2,3 ORDER BY 1,2,3').fetchall(),strict_causality='NOT_GRANTED',complete_session_delta='NOT_CLAIMED',missing_algopack_token='NOT_ACCESSED_NOT_REQUIRED_FOR_ARCHIVE_CONSOLIDATION')
    c.execute("CREATE VIEW positions_normalized AS SELECT * FROM flow_normalized WHERE starts_with(kind,'POSITIONS_') OR (kind='PUBLIC_TRADES_RAW' AND oi IS NOT NULL)")
    c.execute("CREATE VIEW position_changes_observed AS SELECT *,oi-lag(oi) OVER(PARTITION BY symbol,secid,substr(event_time,1,10) ORDER BY event_time,try_cast(json_extract_string(raw,'$.RECNO') AS BIGINT),source_record_key) AS oi_change_observed,'CAPTURED_OBSERVATION_CHANGE_NOT_CONTINUOUS_SESSION' AS change_quality FROM flow_normalized WHERE kind='PUBLIC_TRADES_RAW' AND oi IS NOT NULL")
    (args.database.parent/'flow_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');c.execute('CHECKPOINT');c.close();print(packed(summary))
if __name__=='__main__':main()
