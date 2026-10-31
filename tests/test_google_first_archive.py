from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timedelta
from types import SimpleNamespace
import pytest
from birzha.application.archive_schema import D1_COLUMNS
from birzha.application.google_first_archive import GoogleFirstArchive,MSK
from birzha.application.canonical_market_mirror import MirrorConflict,digest
from birzha.domain.market import Candle,CandleSeries,Instrument
from birzha.storage.google_m1_bytes import merge_zip

NOW=lambda:datetime(2026,10,31,tzinfo=MSK)
CODES={'RI':'RIZ6','SI':'SiZ6','BR':'BRX6','GOLD':'GDZ6','SBER':'SBER','IMOEX':'IMOEX'}
class Book:
    def __init__(self):
        self.data={tf:[D1_COLUMNS+['research_status','normalized_sha256','source_archive_sha256','capture_day_status','source_kind','caveat','source_capture_label','source_file_id']] for tf in ('D1','H1','M15')};self.status={};self.audit=[];self.m1={};self.appends=0;self.fail_append=False
        self.indicators={'formula':'=ARRAYFORMULA(A:A)'}
    def validate_target(self,s,t):assert t in self.data
    def validate_service_target(self,s,t):assert t in ('CONTRACTS','SYNC_STATUS','QUALITY_AUDIT')
    def read_rows(self,s,t):return deepcopy(self.data[t])
    def read_status(self,s):return [['key','value'],*[[k,v] for k,v in self.status.items()]]
    def update_status(self,s,u):self.status.update(u)
    def append_quality(self,s,v):self.audit.append(deepcopy(v))
    def append_rows(self,s,t,rows):
        self.data[t].extend(deepcopy(rows));self.appends+=1
        if self.fail_append:self.fail_append=False;raise RuntimeError('private-upstream-value')
    def validate_m1(self,*a):pass
    def read_m1(self,s,sec,month):return self.m1.get((sec,month))
    def publish_m1(self,s,sec,month,rows):
        old=self.m1.get((sec,month));body,_=merge_zip(old['body'] if old else None,rows,s,sec,month)
        import hashlib
        item={'body':body,'sha256':hashlib.sha256(body).hexdigest(),'file_id':'cloud-zip'};self.m1[(sec,month)]=item;return item
class Market:
    def __init__(self):self.calls=[];self.resolves=[];self.fail=False;self.sparse=False;self.wrong=False
    def resolve(self,s,as_of):
        self.resolves.append((s,as_of));key=s.upper()
        return Instrument(s,CODES[key],'RFUD' if key not in ('SBER','IMOEX') else 'TQBR','futures' if key not in ('SBER','IMOEX') else 'stock','forts' if key not in ('SBER','IMOEX') else 'shares','future' if key not in ('SBER','IMOEX') else ('index' if key=='IMOEX' else 'equity'))
    def candles_for_instrument(self,ins,**kw):
        self.calls.append((ins.secid,kw))
        if self.fail:raise RuntimeError('private-upstream-value')
        start=datetime.fromisoformat(kw['from_date']+'T10:00:00+03:00')
        n=2 if self.sparse else (15 if kw['timeframe']=='M1' else 1)
        candles=tuple(Candle(open=100,close=101,high=102,low=99,volume=None if ins.secid=='IMOEX' else 50,value=3,begin=(start+timedelta(minutes=i)).isoformat(),end=(start+timedelta(minutes=i,seconds=59 if kw['timeframe']=='M1' else (3599 if kw['timeframe']=='H1' else 50399))).isoformat()) for i in range(n))
        return CandleSeries(ins,'H1' if self.wrong else kw['timeframe'],candles)
def setup():
    b=Book();m=Market();s=GoogleFirstArchive(b,m,'a'*40,True,now=NOW);return b,m,s
def call(s,symbol='RI',tf='D1',**kw):return s.collect(symbol=symbol,timeframe=tf,day='2026-10-29',dry_run=False,**kw)

@pytest.mark.parametrize('symbol',list(CODES))
@pytest.mark.parametrize('tf',['D1','H1','M15'])
def test_six_instruments_google_gap_source_append_readback_and_repeat(symbol,tf):
    b,m,s=setup();before=deepcopy(b.indicators)
    assert s.collect(symbol=symbol,timeframe=tf,day='2026-10-29')['status']=='PLANNED'
    assert not b.audit and not b.status and not m.calls and not m.resolves
    r=call(s,symbol,tf);assert r['status']=='READBACK_PASS' and r['added_rows']==1
    calls=len(m.calls);appends=b.appends;assert call(s,symbol,tf)['status']=='REUSED'
    assert len(m.calls)==calls==1 and b.appends==appends and before==b.indicators
    assert b.data[tf][1][8]==CODES[symbol] and b.data[tf][1][2]=='' and b.data[tf][1][7]=='UNKNOWN'
    assert b.data[tf][1][13]==('' if symbol=='IMOEX' else (750 if tf=='M15' else 50))

@pytest.mark.parametrize('tf',['D1','M15'])
def test_lost_candle_write_ack_uses_cloud_prepared_receipt_no_redownload(tf):
    b,m,s=setup();b.fail_append=True
    with pytest.raises(RuntimeError):call(s,tf=tf)
    assert len(b.data[tf])==2
    result=call(s,tf=tf)
    assert result['added_rows']==0 and len(m.calls)==1 and len(b.data[tf])==2
    assert 'private-upstream-value' not in str(b.audit)+str(b.status)

def test_source_timeout_never_blindly_retries():
    b,m,s=setup();m.fail=True
    with pytest.raises(RuntimeError):call(s)
    m.fail=False
    with pytest.raises(MirrorConflict,match='SOURCE_RESULT_UNKNOWN'):call(s)
    assert len(m.calls)==1

def test_partial_m15_uses_cloud_m1_no_source_and_preserves_existing():
    b,m,s=setup();call(s,tf='M15');b.status={k:v for k,v in b.status.items() if not k.startswith('google_first|')}
    b.data['M15']=[b.data['M15'][0]]
    assert call(s,tf='M15')['source_requests']==0 and len(m.calls)==1

def test_sparse_m1_not_fabricated_or_complete():
    b,m,s=setup();m.sparse=True;call(s,tf='M15')
    assert b.data['M15'][1][13]==100 and b.data['M15'][1][16] is False
    assert b.data['M15'][1][18]==2

def test_existing_unproven_day_not_downloaded():
    b,m,s=setup();call(s);b.status={}
    assert call(s)['status']=='EXISTING_UNVERIFIED_NO_FETCH' and len(m.calls)==1

def test_corrupt_cloud_receipt_and_wrong_contract_refused():
    b,m,s=setup();call(s);b.data['D1'][1][12]=100.5
    with pytest.raises(MirrorConflict,match='RECEIPT_CONFLICT'):call(s)
    b,m,s=setup()
    with pytest.raises(MirrorConflict,match='T_MINUS_1'):call(s,exact_secid='RIU6')
    assert not m.calls

@pytest.mark.parametrize('bad',[{'symbol':'RTSI'},{'tf':'W1'},{'day':'2026-10-31'}])
def test_input_fail_closed(bad):
    b,m,s=setup();args={'symbol':'RI','timeframe':'D1','day':'2026-10-29','dry_run':False};args.update({'timeframe' if k=='tf' else k:v for k,v in bad.items()})
    with pytest.raises((MirrorConflict,ValueError)):s.collect(**args)
    assert not m.calls and not b.audit

def test_no_disk_or_duckdb_during_collection(monkeypatch):
    import builtins,pathlib
    b,m,s=setup()
    def denied(*a,**k):raise AssertionError('filesystem access')
    monkeypatch.setattr(builtins,'open',denied);monkeypatch.setattr(pathlib.Path,'read_bytes',denied);monkeypatch.setattr(pathlib.Path,'write_bytes',denied)
    call(s);call(s,tf='M15')

def test_new_day_resolves_contract_at_that_date_and_sole_writer_required():
    b,m,s=setup();s.sole_writer=False
    with pytest.raises(MirrorConflict,match='SOLE_WRITER'):call(s)
    s.sole_writer=True;call(s)
    assert str(m.resolves[0][1])=='2026-10-29'
    assert s.coverage(symbol='RI',timeframes=('D1',))['timeframes']['D1']['last_date']=='2026-10-29'


def test_empty_response_is_not_a_permanent_completed_receipt():
    b,m,s=setup();original=m.candles_for_instrument
    m.candles_for_instrument=lambda ins,**kw:CandleSeries(ins,kw['timeframe'],())
    assert call(s)['status']=='SOURCE_NOT_AVAILABLE'
    assert len(b.data['D1'])==1
    assert call(s)['status']=='SOURCE_NOT_AVAILABLE_RETRY_LATER'
    s.now=lambda:NOW()+timedelta(hours=2)
    m.candles_for_instrument=original
    assert call(s)['status']=='READBACK_PASS' and len(b.data['D1'])==2


def test_committed_receipt_does_not_bypass_explicit_contract_check():
    b,m,s=setup();call(s)
    with pytest.raises(MirrorConflict,match='T_MINUS_1'):call(s,exact_secid='RIU6')
    assert len(m.calls)==1

def test_semantic_duplicate_keys_fail_before_source():
    b,m,s=setup();call(s)
    duplicate=b.data['D1'][1][:];duplicate[4]='2026-10-29T07:00:00+00:00';duplicate[0]='RI|D1|RIZ6|'+duplicate[4]
    b.data['D1'].append(duplicate)
    with pytest.raises(MirrorConflict,match='SEMANTIC_DUPLICATE'):call(s)
    assert len(m.calls)==1

def test_prepared_fragments_retired_after_commit():
    b,m,s=setup();call(s)
    assert all(v=='' for k,v in b.status.items() if '|prepared|' in k)


def test_wrong_day_source_cannot_mutate_monthly_zip():
    from dataclasses import replace
    b,m,s=setup();original=m.candles_for_instrument
    def wrong_day(ins,**kw):
        series=original(ins,**kw)
        return replace(series,candles=tuple(replace(c,begin=c.begin.replace('10-29','10-28'),end=c.end.replace('10-29','10-28')) for c in series.candles))
    m.candles_for_instrument=wrong_day
    with pytest.raises(MirrorConflict,match='SOURCE_TIME_OUTSIDE'):call(s,tf='M15')
    assert b.m1=={} and len(b.data['M15'])==1
