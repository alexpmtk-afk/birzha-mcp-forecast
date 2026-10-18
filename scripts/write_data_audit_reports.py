"""Export aggregate-only coverage tables; no server prices or raw payloads leave C:."""
import argparse,csv,datetime as dt,json
from pathlib import Path
import duckdb

def main():
 p=argparse.ArgumentParser();p.add_argument('--audit',type=Path,required=True);p.add_argument('--research',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))
 def out(name,cols,rows):
  with (a.output/name).open('w',encoding='utf-8-sig',newline='') as f:w=csv.writer(f);w.writerow(cols);w.writerows(rows)
 audit=load(a.audit/'analysis/audit_summary.json');final=load(a.research/'final_summary.json');flow=load(a.research/'flow_summary.json');old=load(a.research/'research_summary.json');cons=load(a.research/'consolidation_summary.json');clock=load(a.research/'publication_receipts.json')
 c=duckdb.connect(str(a.research/'research_data_unification.duckdb'),read_only=True);g=[]
 for b in audit['books']:
  for tf in ('D1','H1','M15'):
   x=b['sheets'][tf]['candles'];g.append([b['symbol'],tf,x['records'],x['first_date'],x['last_date'],x['unique_keys'],x['categories'].get('is_complete',{})])
 complete=c.execute('SELECT symbol,timeframe,count(*),sum(CASE WHEN accepted_complete THEN 1 ELSE 0 END),min(start_time)::VARCHAR,max(end_time)::VARCHAR FROM final_research_inputs GROUP BY 1,2 ORDER BY 1,2').fetchall()
 features=['return_5','return_10','return_20','sma_20','sma_50','efficiency_ratio_20','atr_14_pct','atr_14_sma','volume_ratio_20','vwap_20_candle_approx','price_location_20','support_20_observed_min','resistance_20_observed_max','trend_score_legacy'];fc=[]
 for name in features:
  for row in c.execute('SELECT symbol,timeframe,count(*),count('+name+'),min(CASE WHEN '+name+' IS NOT NULL THEN begin END),max(CASE WHEN '+name+' IS NOT NULL THEN begin END) FROM indicators_final GROUP BY 1,2 ORDER BY 1,2').fetchall():fc.append([row[0],row[1],name,*row[2:],'RECONSTRUCTED_RESEARCH_ONLY'])
 contracts=c.execute('SELECT symbol,secid,timeframe,count(*),min(start_time)::VARCHAR,max(end_time)::VARCHAR,bool_and(accepted_complete) FROM final_research_inputs GROUP BY 1,2,3 ORDER BY 1,2,3').fetchall()
 live=[]
 for file in sorted((a.audit/'home').glob('coverage-*.json')):
  r=load(file);v=r.get('data',{}).get('result',{});live.append([v.get('secid'),v.get('timeframe'),v.get('count'),v.get('first_begin'),v.get('last_end'),r.get('status'),r.get('completedUtc')])
 out('google_candle_coverage.csv',['symbol','timeframe','rows','first','last','unique_keys','source_complete_flags'],g)
 out('final_candle_coverage.csv',['symbol','timeframe','rows','source_flag_complete_rows','first','last'],complete)
 out('indicator_coverage.csv',['symbol','timeframe','indicator','rows','numeric_available','first_numeric_begin','last_numeric_begin','quality'],fc)
 out('contract_coverage.csv',['symbol','secid','timeframe','rows','first','last','source_flags_complete'],contracts)
 out('home_live_contract_coverage.csv',['secid','timeframe','rows','first','last','request_status','checked_utc'],live)
 out('flow_coverage.csv',['scope','symbol','kind','rows','first_event','last_event'],flow['groups'])
 matrix=[[r[0],r[1],'GOOGLE_SNAPSHOT','OHLCV',r[3],r[4],r[2],r[5],'RESEARCH_ONLY'] for r in g]
 matrix += [[r[0],r[1],'FINAL_LOCAL_RESEARCH','OHLCV',r[4],r[5],r[2],r[2],'CALENDAR_AND_RECEIPT_UNATTESTED'] for r in complete]
 matrix += [[r[0],r[1],'FINAL_LOCAL_RESEARCH',r[2],r[5],r[6],r[3],r[4],r[7]] for r in fc]
 matrix += [[r[1],'SOURCE_DEPENDENT',r[0],r[2],r[4],r[5],r[3],r[3],'PARTIAL_NOT_PIT'] for r in flow['groups']]
 out('coverage_matrix.csv',['symbol','timeframe','source','metric','first','last','records','available_or_unique','quality'],matrix)
 source=[dict(symbol=b['symbol'],sha256=b['file_sha256'],matrices={tf:b['sheets'][tf]['matrix_sha256'] for tf in ('D1','H1','M15')}) for b in audit['books']]
 manifest=dict(base_sha=old['code_base_sha'],created_at=dt.datetime.now(dt.timezone.utc).isoformat(),google_sources=source,home_backup=load(a.audit/'accepted_backup_aggregate.json'),tail_receipts=final['source_receipts'],formula_bundle=old['formula_bundle_version'],replay=cons['source_indicator_replay'],final_indicator_sha256=final['indicator_sha256'],flow_sha256=flow['sha256'],clock_receipts=clock)
 (a.output/'source_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
 summary=dict(google=g,final=complete,flow=flow['groups'],numeric_values=sum(r[4] for r in fc),contract_pairs=len(contracts),live_pairs=len(live),flow_sha256=flow['sha256'],indicator_sha256=final['indicator_sha256'],replay_sha256=cons['source_indicator_replay']['sha256'],sources=source,formula_bundle=old['formula_bundle_version'])
 (a.output/'report_data.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');c.close();print(json.dumps(dict(final_rows=final['final_candles'],contract_pairs=len(contracts),live_pairs=len(live),numeric_values=summary['numeric_values'],matrix_rows=len(matrix))))
if __name__=='__main__':main()
