from copy import deepcopy
from dataclasses import asdict
from datetime import date,datetime,timedelta
import json
from types import SimpleNamespace
import pytest
from birzha.application.archive_schema import D1_COLUMNS
from birzha.application.google_first_archive import ARCHIVE_EXPIRY_POLICY,GoogleFirstArchive,MSK
from birzha.providers.moex_history import MoexHistoricalFutureResolver
from birzha.application.canonical_market_mirror import MirrorConflict,digest
from birzha.domain.market import Candle,CandleSeries,Instrument
from birzha.storage.google_m1_bytes import merge_zip

NOW=lambda:datetime(2026,10,31,tzinfo=MSK)
CODES={'RI':'RIZ6','SI':'SiZ6','BR':'BRX6','GOLD':'GDZ6','SBER':'SBER','IMOEX':'IMOEX'}
RI_CONTRACTS = [
    ['symbol','secid','board','engine','market','asset_class','valid_from','valid_to','expiration'],
    ['RI','RIH5','rfud','futures','forts','FUTURES','2023-03-10','2025-03-20','2025-03-20'],
    ['RI','RIZ5','rfud','futures','forts','FUTURES','2023-12-15','2025-12-18','2025-12-18'],
    ['RI','RIH6','rfud','futures','forts','FUTURES','2024-03-15','2026-03-19','2026-03-19'],
    ['RI','RIM6','rfud','futures','forts','FUTURES','2024-06-14','2026-06-18','2026-06-18'],
    ['RI','RIU6','rfud','futures','forts','FUTURES','2024-09-13','2026-09-17','2026-09-17'],
    ['RI','RIZ6','rfud','futures','forts','FUTURES','2024-12-13','2026-12-17','2026-12-17'],
]

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
    def resolve_archive_contract(self,s,*,as_of):
        key=s.upper()
        if key=='RI':
            valid=[r for r in RI_CONTRACTS[1:] if date.fromisoformat(r[6])<=as_of<=date.fromisoformat(r[8])]
            selected=min(valid,key=lambda r:r[8])
            secid,expiry=selected[1],selected[8]
        else:
            secid,expiry=CODES[key],'2026-12-31'
        return Instrument(s,secid,'RFUD','futures','forts','future',
                          last_trade_date=expiry,roll_policy=ARCHIVE_EXPIRY_POLICY)
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
    with pytest.raises(MirrorConflict,match='ARCHIVE_EXPIRY_SELECTION'):call(s,exact_secid='RIU6')
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
    assert not m.resolves and m.calls[0][1]['from_date']=='2026-10-29'
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
    with pytest.raises(MirrorConflict,match='ARCHIVE_EXPIRY_SELECTION'):call(s,exact_secid='RIU6')
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


def test_managed_cloud_scan_cursor_progress_and_safe_restart():
    b,m,s=setup()
    assert s.coverage(symbol='RI',timeframes=('D1',))['timeframes']['D1']['archive_start']=='2025-01-01'
    assert s.coverage(symbol='RI',timeframes=('D1',))['timeframes']['D1']['scan_cursor']=='2025-01-01'
    b.status['google_first_scan_cursor|D1']='2026-10-29'
    before=dict(b.status)
    assert s.sync(symbol='RI',from_date='2026-10-29',till_date='2026-10-29',
                  timeframes=('D1',),dry_run=True,scan_mode=True)['status']=='PLANNED'
    assert b.status==before and not m.calls
    r=s.sync(symbol='RI',from_date='2026-10-29',till_date='2026-10-29',
             timeframes=('D1',),dry_run=False,scan_mode=True)
    assert r['status']=='PASS' and len(m.calls)==1
    assert b.status['google_first_scan_cursor|D1']=='2026-10-30'
    assert s.coverage(symbol='RI',timeframes=('D1',))['timeframes']['D1']['scan_cursor']=='2026-10-30'
    with pytest.raises(MirrorConflict,match='SCAN_CURSOR_MISMATCH'):
        s.sync(symbol='RI',from_date='2026-10-29',till_date='2026-10-29',
               timeframes=('D1',),dry_run=False,scan_mode=True)
    assert len(m.calls)==1


def test_managed_cursor_advances_safely_after_empty_and_unverified_day():
    b,m,s=setup()
    b.status['google_first_scan_cursor|D1']='2026-10-29'
    m.candles_for_instrument=lambda ins,**kw:CandleSeries(ins,kw['timeframe'],())
    r=s.sync(symbol='RI',from_date='2026-10-29',till_date='2026-10-29',
             timeframes=('D1',),dry_run=False,scan_mode=True)
    assert r['status']=='PARTIAL' and r['results'][0]['status']=='SOURCE_NOT_AVAILABLE'
    assert b.status['google_first_scan_cursor|D1']=='2026-10-30'
    assert not b.data['D1'][1:]


def test_managed_cursor_never_advances_after_unknown_source():
    b,m,s=setup();b.status['google_first_scan_cursor|D1']='2026-10-29'
    m.fail=True
    with pytest.raises(RuntimeError):
        s.sync(symbol='RI',from_date='2026-10-29',till_date='2026-10-29',
               timeframes=('D1',),dry_run=False,scan_mode=True)
    assert b.status['google_first_scan_cursor|D1']=='2026-10-29'
    assert b.status['google_first_status']=='ERROR_RECONCILE_BEFORE_RETRY'


def test_full_horizon_resets_after_last_moscow_day_without_local_state():
    b,m,s=setup()
    b.status['google_first_scan_cursor|D1']='2026-10-31'  # later than 30 October
    assert s.coverage(symbol='RI',timeframes=('D1',))['timeframes']['D1']['scan_cursor']=='2025-01-01'
    with pytest.raises(MirrorConflict,match='SCAN_CURSOR_INVALID'):
        b.status['google_first_scan_cursor|D1']='not a day'
        s.coverage(symbol='RI',timeframes=('D1',))


class Model3Book(Book):
    from test_model3_google_backfill import Book as IndicatorBook
    from birzha.application.model3_indicator_coverage import COMPACT_RI_INDICATOR_COLUMNS
    writer_guard = IndicatorBook.writer_guard
    read_service_rows = IndicatorBook.read_service_rows
    model3_append_rows = IndicatorBook.model3_append_rows
    def __init__(self, timeframe='D1'):
        super().__init__()
        self.indicator_sheet = timeframe + '_INDICATORS'
        self.features = [list(self.COMPACT_RI_INDICATOR_COLUMNS)]
        self.writes = 0
        self.fail_after_commit = False

    def validate_model3_target(self, symbol, sheet):
        assert (symbol, sheet) == ('RI', self.indicator_sheet)

    def read_rows(self, symbol, sheet):
        if sheet == self.indicator_sheet:
            return deepcopy(self.features)
        return super().read_rows(symbol, sheet)


MODEL3_WINDOW = dict(symbol='RI', from_date='2026-10-29', till_date='2026-10-29',
                     timeframes=('D1',))
MODEL3_APPROVED = dict(dry_run=False, model3_mode='apply', owner_confirmed=True,
                       external_writer_exclusion_confirmed=True)


@pytest.mark.parametrize('timeframe', ['D1', 'H1', 'M15'])
def test_sync_appends_indicators_after_candle_readback_and_reuses_both(timeframe):
    book, market = Model3Book(timeframe), Market()
    window = dict(MODEL3_WINDOW, timeframes=(timeframe,))
    service = GoogleFirstArchive(book, market, 'a'*40, True, now=NOW)
    first = service.sync(**window, **MODEL3_APPROVED)
    assert first['results'][0]['status'] == 'READBACK_PASS'
    assert first['indicator_results'][0]['confirmed_rows'] == 1
    assert book.writes == book.appends == len(market.calls) == 1
    assert first['written'] is True
    old = deepcopy(book.features)
    again = service.sync(**window, **MODEL3_APPROVED)
    assert again['results'][0]['status'] == 'REUSED'
    assert again['indicator_results'][0]['confirmed_rows'] == 0
    assert again['written'] is False
    assert book.features == old
    assert book.writes == book.appends == len(market.calls) == 1


def test_sync_recovers_lost_indicator_reply_without_fetch_or_append():
    book, market = Model3Book(), Market()
    book.fail_after_commit = True
    book.status['google_first_scan_cursor|D1'] = '2026-10-29'
    service = GoogleFirstArchive(book, market, 'a'*40, True, now=NOW)
    args = dict(MODEL3_WINDOW, **MODEL3_APPROVED, scan_mode=True)
    first = service.sync(**args)
    assert first['status'] == 'PARTIAL' and first['written'] == 'UNKNOWN'
    assert first['results'][0]['status'] == 'READBACK_PASS'
    assert book.status['google_first_scan_cursor|D1'] == '2026-10-29'
    second = service.sync(**args)
    assert second['indicator_results'][0]['status'] == 'RECONCILED'
    assert book.status['google_first_scan_cursor|D1'] == '2026-10-29'
    third = service.sync(**args)
    assert third['indicator_results'][0]['status'] in ('CURRENT', 'FIELD_REVIEW_REQUIRED')
    assert book.status['google_first_scan_cursor|D1'] == '2026-10-30'
    assert book.writes == book.appends == len(market.calls) == 1


def test_sync_preview_detects_missing_indicator_on_saved_candle():
    book, market = Model3Book(), Market()
    service = GoogleFirstArchive(book, market, 'a'*40, True, now=NOW)
    call(service)
    book.status['google_first_scan_cursor|D1'] = '2026-10-29'
    before = deepcopy((book.data, book.features, book.status, book.audit))
    preview = service.sync(**MODEL3_WINDOW, model3_mode='preview', scan_mode=True)
    assert preview['indicator_results'][0]['status'] == 'PENDING_ROWS'
    assert preview['written'] is False
    assert (book.data, book.features, book.status, book.audit) == before
    real = service.sync(**MODEL3_WINDOW, model3_mode='preview', scan_mode=True, dry_run=False)
    assert real['next_day'] == '2026-10-29'
    assert book.status['google_first_scan_cursor|D1'] == '2026-10-29'
    assert not book.writes and len(market.calls) == 1


def test_sync_apply_requires_exclusion_before_candle_stage():
    book, market = Model3Book(), Market()
    service = GoogleFirstArchive(book, market, 'a'*40, True, now=NOW)
    approval = dict(MODEL3_APPROVED, external_writer_exclusion_confirmed=False)
    with pytest.raises(MirrorConflict, match='EXTERNAL_EXCLUSION_REQUIRED'):
        service.sync(**MODEL3_WINDOW, **approval)
    assert not market.calls and not book.audit and not book.status


def test_sync_uses_december_candle_for_january_field_plan():
    from test_model3_field_repair import PatchBook, blank, service as repair_service

    book = PatchBook()
    header = book.source[0]
    december = dict(zip(header, book.source[-1]))
    december.update(calendar_date='2025-12-30',
                    bar_start_time='2025-12-30T00:00:00+03:00',
                    bar_end_time='2025-12-30T23:59:59+03:00',
                    record_key='RI|D1|RIH6|2025-12-30T00:00:00+03:00', high=149)
    book.source.insert(-1, [december[k] for k in header])
    blank(book.features, 'p3_prior_high')
    service = repair_service(book)
    service.collect = lambda **kwargs: {'status':'REUSED','written':False}
    before = deepcopy(book.features)
    preview = service.sync(symbol='RI', from_date='2026-01-05', till_date='2026-01-05',
                           timeframes=('D1',), model3_mode='preview')
    assert preview['indicator_results'][0]['status'] == 'FIELD_REVIEW_REQUIRED'
    report = service.model3_repair_fields(timeframe='D1', from_date='2026-01-05',
                                          till_date='2026-01-05', fields=['p3_prior_high'])
    assert report['gaps'][0]['value'] == 149
    assert book.features == before and not book.writes and not book.audit


@pytest.mark.parametrize('tf', ['D1','H1','M15'])
def test_ri_uses_one_quarterly_contract_through_expiry_for_all_frames(tf):
    book,market,service=setup()
    expiry='2026-06-18'
    key=f'google_first|RI|{tf}|{expiry}'
    book.status[key]=json.dumps({'phase':'COMMITTED','secid':'RIU6','rows_sha256':'legacy'})
    for day,secid in [('2026-06-17','RIM6'),(expiry,'RIM6'),('2026-06-19','RIU6')]:
        preview=service.collect(symbol='RI',timeframe=tf,day=day)
        assert preview['secid']==secid
        result=service.collect(symbol='RI',timeframe=tf,day=day,dry_run=False)
        assert result['secid']==secid
        assert service.collect(symbol='RI',timeframe=tf,day=day,dry_run=False)['status']=='REUSED'
    assert [secid for secid,_ in market.calls]==['RIM6','RIM6','RIU6']
    assert {x['timeframe'] for _,x in market.calls}=={'M1' if tf=='M15' else tf}
    assert json.loads(book.status[key])['secid']=='RIU6'
    assert not market.resolves


@pytest.mark.parametrize('root,old,new,asset',[
    ('RI','RIH6','RIM6','RTS'),('Si','SiH6','SiM6','Si'),
    ('BR','BRH6','BRJ6','BR'),('GOLD','GDH6','GDM6','GOLD'),
])
def test_archive_expiry_resolver_keeps_old_series_even_if_next_volume_is_higher(root,old,new,asset):
    def row(code, expiry, volume):
        return dict(SECID=code,ASSETCODE=asset,BOARDID='RFUD',VOLUME=volume,LASTTRADEDATE=expiry)
    rows=[row(old,'2026-03-19',2),row(new,'2026-04-20',10000),
          row(old+'-'+new,'2026-03-19',900000),row('OTHERH6','2026-03-18',1000000)]
    resolver=MoexHistoricalFutureResolver(None)
    resolver._previous_observed_session=lambda symbol,day:(day-timedelta(days=1),rows)
    assert resolver.resolve_archive_expiry(root,date(2026,3,19)).secid==old
    result=resolver.resolve_archive_expiry(root,date(2026,3,20))
    assert result.secid==new and result.roll_policy==ARCHIVE_EXPIRY_POLICY
