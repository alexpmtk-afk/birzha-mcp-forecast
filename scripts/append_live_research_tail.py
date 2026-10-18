"""Append a read-only live-source tail to the private research union, preserving old rows."""
import argparse,datetime as dt,gzip,hashlib,json
from pathlib import Path
import duckdb
from build_research_data_unification import indicator_records,js,sqls,sqlcols

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--research',type=Path,required=True);a=ap.parse_args()
 if a.research.resolve().drive.lower()!='c:':raise ValueError('Private local output required')
 c=duckdb.connect(str(a.research/'research_data_unification.duckdb'));old=json.loads((a.research/'research_summary.json').read_text(encoding='utf-8'))
 if 'final_research_inputs' in {r[0] for r in c.execute('SHOW TABLES').fetchall()}:raise ValueError('Existing final run must not be overwritten')
 cols=[r[0] for r in c.execute('DESCRIBE consolidated_inputs').fetchall()];types={r[0]:r[1] for r in c.execute('DESCRIBE consolidated_inputs').fetchall()};records=[];receipts=[]
 for file in sorted((a.research/'home_tail').glob('*.json')):
  envelope=json.loads(file.read_text(encoding='utf-8-sig'));s=envelope['data']['result']
  if s['status'] not in ('STORED_SERIES_READ','STORED_SERIES_EMPTY'):raise ValueError('Source read refused')
  ins=s['instrument'];secid=ins['secid'];sym=ins['root_symbol'] or ins['symbol'];sym='GOLD' if secid.startswith('GD') else 'BR' if secid.startswith('BR') else 'Si' if secid.startswith('Si') else secid
  receipts.append(dict(file=file.name,sha256=hashlib.sha256(file.read_bytes()).hexdigest(),count=len(s['candles']),completedUtc=envelope['completedUtc']))
  for bar in s['candles']:
   begin=dt.datetime.fromisoformat(bar['begin'].replace('Z','+00:00')).replace(tzinfo=None);end=dt.datetime.fromisoformat(bar['end'].replace('Z','+00:00')).replace(tzinfo=None)
   sha=hashlib.sha256(js(dict(instrument=ins,bar=bar,timeframe=s['timeframe'])).encode()).hexdigest()
   r={k:None for k in cols};r.update(input_scope='HOME_LIVE_READ_TAIL',symbol=sym,secid=secid,timeframe=s['timeframe'],start_time=begin.isoformat(),end_time=end.isoformat(),raw_begin=bar['begin'],raw_end=bar['end'],open=bar['open'],high=bar['high'],low=bar['low'],close=bar['close'],volume=bar['volume'],turnover=bar['value'],complete=bar['completed'],available_at=bar['available_at'],available_at_confidence=bar['available_at_confidence'],observed_at=bar['observed_at'],record_key=f"{sym}|{s['timeframe']}|{secid}|{bar['begin']}",input_row_sha256=sha,source_quality='STORED_PROVIDER_TAIL_NOT_PIT_ATTESTED',aliases=1,accepted_complete=bar['completed'],lineage_sha256=sha)
   records.append(r)
 p=a.research/'latest_tail_inputs.jsonl.gz'
 with gzip.open(p,'wt',encoding='utf-8') as f:
  for r in records:f.write(js(r)+'\n')
 c.execute('CREATE TABLE latest_tail_inputs AS SELECT * FROM read_json('+sqls(p)+",format='newline_delimited',columns="+sqlcols(types)+')')
 join='g.symbol=h.symbol AND g.secid=h.secid AND g.timeframe=h.timeframe AND g.start_time=h.start_time'
 bad=c.execute('SELECT count(*) FROM consolidated_inputs g JOIN latest_tail_inputs h ON '+join+' WHERE g."open" IS DISTINCT FROM h."open" OR g."high" IS DISTINCT FROM h."high" OR g."low" IS DISTINCT FROM h."low" OR g."close" IS DISTINCT FROM h."close" OR g.end_time IS DISTINCT FROM h.end_time OR (g.symbol NOT IN (\'IMOEX\',\'RTSI\') AND g.volume IS DISTINCT FROM h.volume)').fetchone()[0]
 if bad:raise ValueError(f'{bad} existing-tail source conflicts')
 c.execute("CREATE TABLE final_research_inputs AS SELECT * REPLACE('FINAL_RECONSTRUCTED_RESEARCH' AS input_scope) FROM consolidated_inputs UNION ALL SELECT h.* REPLACE('FINAL_RECONSTRUCTED_RESEARCH' AS input_scope) FROM latest_tail_inputs h WHERE NOT EXISTS(SELECT 1 FROM consolidated_inputs g WHERE "+join+')')
 c.execute("CREATE VIEW canonical_candles_research AS SELECT * REPLACE(CASE WHEN symbol IN ('IMOEX','RTSI') THEN NULL ELSE volume END AS volume),volume AS raw_volume,CASE WHEN symbol IN ('IMOEX','RTSI') THEN 'NOT_APPLICABLE' ELSE 'UNKNOWN' END AS volume_unit,false AS strict_forecast_admission FROM final_research_inputs")
 groups=c.execute('SELECT symbol,timeframe,count(*),min(start_time)::VARCHAR,max(end_time)::VARCHAR FROM final_research_inputs GROUP BY 1,2 ORDER BY 1,2').fetchall()
 calculated_now=dt.datetime.now(dt.timezone.utc).isoformat()
 c.execute('CREATE TEMP VIEW feature_inputs AS SELECT * FROM final_research_inputs');path=a.research/'indicators_final.jsonl.gz';digest=hashlib.sha256();n=0
 with gzip.open(path,'wt',encoding='utf-8') as f:
  for r in indicator_records(c,old['formula_bundle_version'],calculated_now):
   f.write(js(r)+'\n');digest.update((js({k:v for k,v in r.items() if k!='calculated_at'})+'\n').encode());n+=1
   if n%50000==0:print('FINAL_INDICATORS',n,flush=True)
 types={r[0]:r[1] for r in c.execute('DESCRIBE indicators').fetchall()};c.execute('CREATE TABLE indicators_final AS SELECT * FROM read_json('+sqls(path)+",format='newline_delimited',columns="+sqlcols(types)+')')
 back=c.execute('SELECT count(*),count(DISTINCT record_key) FROM indicators_final').fetchone()
 if list(back)!=[n,n]:raise ValueError('Final readback failed')
 summary=dict(schema='FINAL_RECONSTRUCTED_RESEARCH_V1',groups=groups,tail_source_rows=len(records),source_receipts=receipts,tail_conflicts=bad,final_candles=n,indicator_records=n,backread=back,indicator_sha256=digest.hexdigest(),calendar='UNATTESTED',historical_receipt='UNATTESTED',strict_admission='NOT_GRANTED',production_writes=0)
 c.execute('CHECKPOINT');c.close();(a.research/'final_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(js(summary),flush=True)
if __name__=='__main__':main()
