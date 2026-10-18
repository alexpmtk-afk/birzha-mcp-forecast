"""Create a bounded Google-only SBER pilot projection. Never exports HOME rows."""
import argparse,gzip,hashlib,json
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--research',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
 names=['record_key','symbol','secid','timeframe','begin','end','source_record_key','source_record_sha256','source_window_sha256','source_window_count','window_start','window_end','formula_id','calculated_at','available_at','available_at_confidence','quality_status','calendar_status','source_complete_flag','indicator_status','return_5','return_10','return_20','sma_20','sma_50','efficiency_ratio_20','atr_14_pct','atr_14_sma','volume_ratio_20','vwap_20_candle_approx','price_location_20','support_20_observed_min','resistance_20_observed_max','trend_score_legacy']
 rows=[];formula=None
 for line in gzip.open(args.research/'indicators.jsonl.gz','rt',encoding='utf-8'):
  r=json.loads(line)
  if r['input_scope']!='GOOGLE_SNAPSHOT' or r['symbol']!='SBER':continue
  assert r['input_scope']=='GOOGLE_SNAPSHOT'
  formula=r['formula_bundle_version'];r['formula_id']=hashlib.sha256(formula.encode()).hexdigest()[:16]
  row=[r.get(n) for n in names];row[names.index('indicator_status')]=json.dumps(r['indicator_status'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
  rows.append(row)
 assert len(rows)==43727
 args.out.mkdir(parents=True,exist_ok=True)
 all_rows=[names]+rows;chunks=[]
 def cell(x):
  if x is None:return {}
  return {'userEnteredValue':{'boolValue' if isinstance(x,bool) else 'numberValue' if isinstance(x,(float,int)) else 'stringValue':x}}
 for start in range(0,len(all_rows),750):
  chunk=all_rows[start:start+750];body={'requests':[{'updateCells':{'range':{'sheetId':2101800015,'startRowIndex':start,'endRowIndex':start+len(chunk),'startColumnIndex':0,'endColumnIndex':34},'rows':[{'values':[cell(v) for v in row]} for row in chunk],'fields':'userEnteredValue'}}]}
  name=f'indicators-{start:06d}.json';text=json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False);(args.out/name).write_text(text,encoding='utf-8');chunks.append({'file':name,'start':start,'rows':len(chunk),'bytes':len(text.encode())})
 manifest=dict(scope='GOOGLE_SNAPSHOT_SBER_ONLY',rows=len(rows),columns=names,formula_bundle=formula,formula_id=hashlib.sha256(formula.encode()).hexdigest()[:16],chunks=chunks)
 (args.out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'rows':len(rows),'chunks':len(chunks),'maximum_bytes':max(c['bytes'] for c in chunks),'formula_id':manifest['formula_id']}))
if __name__=='__main__':main()
