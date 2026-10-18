"""Merge research observations without overwriting either source; replay accepted formulas."""
import argparse,datetime as dt,gzip,hashlib,json
from pathlib import Path
import duckdb
from build_research_data_unification import indicator_records,js,sqls,sqlcols

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--research',type=Path,required=True);a=ap.parse_args()
 if a.research.resolve().drive.lower()!='c:':raise ValueError('HOME-derived research must remain private and local')
 old=json.loads((a.research/'research_summary.json').read_text(encoding='utf-8'));c=duckdb.connect(str(a.research/'research_data_unification.duckdb'))
 bad=c.execute("SELECT count(*) FROM feature_inputs WHERE symbol IN ('Si','BR','GOLD') AND NOT regexp_full_match(secid,'(Si|BR|GD)[A-Z][0-9]{1,2}')").fetchone()[0]
 if bad:raise ValueError(f'{bad} unresolved futures contract identifiers')
 if 'consolidated_inputs' in {r[0] for r in c.execute('SHOW TABLES').fetchall()}:raise ValueError('Existing consolidated run; verify rather than overwrite')
 # Reproduction is an engineering/data check; no forecast, fitting or outcomes.
 digest=hashlib.sha256();n=0
 for r in indicator_records(c,old['formula_bundle_version'],old['calculated_at']):
  digest.update((js({k:v for k,v in r.items() if k!='calculated_at'})+'\n').encode());n+=1
 if digest.hexdigest()!=old['indicator_values_sha256_excluding_calculation_time']:raise ValueError('Indicator replay differs')
 print('SOURCE_REPLAY_MATCH',n,flush=True)
 conflicts=c.execute("""SELECT count(*) FROM feature_inputs g JOIN feature_inputs h ON g.symbol=h.symbol AND g.secid=h.secid AND g.timeframe=h.timeframe AND g.start_time=h.start_time WHERE g.input_scope='GOOGLE_SNAPSHOT' AND h.input_scope='HOME_ACCEPTED_BACKUP' AND (g."open" IS DISTINCT FROM h."open" OR g."high" IS DISTINCT FROM h."high" OR g."low" IS DISTINCT FROM h."low" OR g."close" IS DISTINCT FROM h."close" OR g.end_time IS DISTINCT FROM h.end_time OR (g.symbol NOT IN ('IMOEX','RTSI') AND g.volume IS DISTINCT FROM h.volume))""").fetchone()[0]
 if conflicts:raise ValueError('Unresolved source price/interval/volume conflicts')
 c.execute("""CREATE TABLE consolidated_inputs AS
 SELECT g.* REPLACE('CONSOLIDATED_RESEARCH' AS input_scope,
   coalesce(g.accepted_complete,false) AND coalesce(h.accepted_complete,g.accepted_complete,false) AS accepted_complete,
   sha256(g.lineage_sha256||'|'||coalesce(h.lineage_sha256,'NO_HOME_MATCH')) AS lineage_sha256)
 FROM feature_inputs g LEFT JOIN feature_inputs h ON h.input_scope='HOME_ACCEPTED_BACKUP' AND g.symbol=h.symbol AND g.secid=h.secid AND g.timeframe=h.timeframe AND g.start_time=h.start_time
 WHERE g.input_scope='GOOGLE_SNAPSHOT'
 UNION ALL SELECT h.* REPLACE('CONSOLIDATED_RESEARCH' AS input_scope) FROM feature_inputs h
 WHERE h.input_scope='HOME_ACCEPTED_BACKUP' AND NOT EXISTS(SELECT 1 FROM feature_inputs g WHERE g.input_scope='GOOGLE_SNAPSHOT' AND g.symbol=h.symbol AND g.secid=h.secid AND g.timeframe=h.timeframe AND g.start_time=h.start_time)""")
 groups=c.execute('SELECT symbol,timeframe,count(*),min(start_time)::VARCHAR,max(end_time)::VARCHAR FROM consolidated_inputs GROUP BY 1,2 ORDER BY 1,2').fetchall()
 c.execute('CREATE TEMP VIEW feature_inputs AS SELECT * FROM consolidated_inputs')
 path=a.research/'indicators_consolidated.jsonl.gz';digest2=hashlib.sha256();count=0
 calculated_now=dt.datetime.now(dt.timezone.utc).isoformat()
 with gzip.open(path,'wt',encoding='utf-8') as f:
  for r in indicator_records(c,old['formula_bundle_version'],calculated_now):
   f.write(js(r)+'\n');digest2.update((js({k:v for k,v in r.items() if k!='calculated_at'})+'\n').encode());count+=1
   if count%50000==0:print('CONSOLIDATED_INDICATORS',count,flush=True)
 types={r[0]:r[1] for r in c.execute('DESCRIBE indicators').fetchall()}
 c.execute('CREATE TABLE indicators_consolidated AS SELECT * FROM read_json('+sqls(path)+",format='newline_delimited',columns="+sqlcols(types)+')')
 backread=c.execute('SELECT count(*),count(DISTINCT record_key) FROM indicators_consolidated').fetchone()
 if list(backread)!=[count,count]:raise ValueError('Consolidated readback failed')
 summary=dict(schema='CONSOLIDATED_RESEARCH_V1',conflicting_source_prices=conflicts,unresolved_contract_codes=bad,source_indicator_replay=dict(rows=n,sha256=digest.hexdigest(),matched=True),consolidated_candles=sum(r[2] for r in groups),groups=groups,indicator_rows=count,readback=backread,indicator_sha256=digest2.hexdigest(),google_rows_unchanged=True,source_precedence='GOOGLE_PRESERVED_WHERE_OVERLAPPING_HOME_BACKUP_ONLY_FOR_MISSING_IDENTITIES',calendar='UNATTESTED',historical_receipt='UNATTESTED',model_admission='RESEARCH_ONLY',production_writes=0)
 c.execute('CHECKPOINT');c.close();(a.research/'consolidation_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(js(summary),flush=True)
if __name__=='__main__':main()
