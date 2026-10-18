"""Finalize research calculation clocks; retain original files and deterministic value hashes."""
import datetime as dt,gzip,hashlib,json
from pathlib import Path
import duckdb
from build_research_data_unification import js

def main(root):
 root=root.resolve()
 if root.drive.lower()!='c:':raise ValueError('Private local research only')
 now=dt.datetime.now(dt.timezone.utc).isoformat();c=duckdb.connect(str(root/'research_data_unification.duckdb'));receipts=[]
 for stem,table in [('indicators_consolidated','indicators_consolidated'),('indicators_final','indicators_final')]:
  source=root/(stem+'.jsonl.gz');dest=root/(stem+'_published.jsonl.gz')
  if dest.exists():raise ValueError('Published research version exists')
  before=set();digest=hashlib.sha256();n=0
  with gzip.open(source,'rt',encoding='utf-8') as f,gzip.open(dest,'wt',encoding='utf-8') as out:
   for line in f:
    r=json.loads(line);before.add(r['calculated_at']);r['calculated_at']=now;out.write(js(r)+'\n');digest.update((js({k:v for k,v in r.items() if k!='calculated_at'})+'\n').encode());n+=1
  c.execute('UPDATE '+table+' SET calculated_at=?',[now]);receipts.append(dict(table=table,rows=n,prior_calculated_at=sorted(before),calculated_at=now,clock_basis='BATCH_FINALIZATION_AFTER_CALCULATION_AND_VALIDATION_NOT_HISTORICAL_RECEIPT',source_file=source.name,published_file=dest.name,deterministic_values_sha256=digest.hexdigest(),published_file_sha256=hashlib.sha256(dest.read_bytes()).hexdigest()))
 c.execute('CHECKPOINT');c.close();(root/'publication_receipts.json').write_text(json.dumps(receipts,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps([dict(table=r['table'],rows=r['rows'],clock_basis=r['clock_basis']) for r in receipts]))
if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('--research',type=Path,required=True);main(p.parse_args().research)
