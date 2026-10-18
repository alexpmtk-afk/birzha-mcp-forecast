import sys,datetime as dt
from pathlib import Path
sys.path.insert(0,str(Path('scripts').resolve()))
import duckdb
from build_research_data_unification import indicator_records
import pytest

def make_db(rows):
 c=duckdb.connect(':memory:')
 c.execute('CREATE TABLE feature_inputs(input_scope VARCHAR,symbol VARCHAR,secid VARCHAR,timeframe VARCHAR,start_time TIMESTAMP,end_time TIMESTAMP,"open" DOUBLE,"high" DOUBLE,"low" DOUBLE,"close" DOUBLE,volume DOUBLE,turnover DOUBLE,accepted_complete BOOLEAN,lineage_sha256 VARCHAR,record_key VARCHAR,input_row_sha256 VARCHAR,raw_begin VARCHAR,raw_end VARCHAR)')
 for i,complete,volume in rows:
  begin=dt.datetime(2025,1,1)+dt.timedelta(days=i);end=begin+dt.timedelta(hours=23)
  c.execute('INSERT INTO feature_inputs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',['TEST','SBER','SBER','D1',begin,end,100+i,102+i,99+i,101+i,volume,1000,complete,str(i),str(i),str(i),begin.isoformat(),end.isoformat()])
 return c

def test_warmup_and_incomplete_reset():
 c=make_db([(i,i!=25,100) for i in range(60)]);r=list(indicator_records(c,'formula','time'))
 assert r[18]['sma_20'] is None and r[19]['sma_20'] is not None
 assert r[25]['sma_20'] is None and r[25]['source_window_count']==0
 assert r[44]['sma_20'] is None and r[45]['sma_20'] is not None
 assert all(x['sma_50'] is None for x in r)
 assert all(x['available_at'] is None for x in r)
 c.close()

def test_missing_volume_does_not_compress_window_and_future_does_not_change_past():
 c=make_db([(i,True,None if i==22 else 100) for i in range(70)]);r=list(indicator_records(c,'formula','time'))
 assert r[21]['volume_ratio_20'] is not None and r[22]['volume_ratio_20'] is None
 assert r[41]['vwap_20_candle_approx'] is None and r[42]['vwap_20_candle_approx'] is not None
 assert r[-1]['source_window_count']==51
 c2=make_db([(i,True,None if i==22 else 100) for i in range(50)])
 assert r[:50]==list(indicator_records(c2,'formula','time'))
 c.close();c2.close()

def test_delta_unknown_volume_and_excel_clock_are_explicit():
 from consolidate_research_flow import delta_values,stamp,symbol
 assert delta_values(10,4,6)==(6,0.3)
 assert delta_values(None,4,0)==(None,None)
 assert stamp(46244)=='2026-08-10T00:00:00'
 assert symbol('GOLD')=='GOLD' and symbol('GDZ6')=='GOLD'
