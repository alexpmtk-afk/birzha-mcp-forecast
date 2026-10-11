from dataclasses import replace
from datetime import datetime,timedelta,timezone
import hashlib,json,subprocess,sys
import pytest
from birzha.domain.market import Candle,CandleSeries
from test_production_feature_contracts import instrument
from test_g2_real_source_level_report import archive,AT
from birzha.application.candle_profile_research import build_candle_profile_research,render_candle_profile_research
from birzha.application.level_reaction import canonical_bytes
from scripts.g2_candle_profile_research import compute

SEEN='2026-03-04T00:00:00Z'
ANALYSIS='2026-03-05T00:00:00Z'


def series(n=60):
    first=datetime(2026,3,1,tzinfo=timezone.utc)
    rows=[]
    for i in range(n):
        begin=first+timedelta(hours=i);end=begin+timedelta(minutes=59,seconds=59)
        p=100.+i
        rows.append(Candle(open=p,close=p,high=p+1,low=p-1,volume=100.,value=1000.,begin=begin.isoformat(),end=end.isoformat(),completed=True,observed_at=SEEN))
    return CandleSeries(instrument(),'H1',tuple(rows))


def report(s=None,**kwargs):
    return build_candle_profile_research(s or series(),analysis_at=ANALYSIS,source_observed_at=SEEN,**kwargs)


def schedule(s):
    selected=s.candles[-50:]
    return {'version':'EXPECTED_BAR_SCHEDULE_V1','origin':'EXTERNAL_EXPECTED_SCHEDULE','complete':True,
        'symbol':s.instrument.symbol,'secid':s.instrument.secid,'timeframe':'H1','source':'SYNTHETIC_DECLARED',
        'observed_at':SEEN,'window_start':selected[0].begin,'window_end':selected[-1].end,
        'expected_bars':[{'begin':c.begin,'end':c.end} for c in selected]}


def test_50_selected_bars_approximate_only_source_immutable():
    s=series();before=canonical_bytes(s.to_dict());r=report(s)
    assert r['status']=='AVAILABLE_APPROXIMATE' and r['selected_bar_count']==50 and r['source_bar_count']==60
    assert r['window_begin']==s.candles[10].begin and r['profile']['total_volume']==5000.
    assert r['profile']['method']=='CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1'
    assert r['declared_schedule']['reason']=='EXPECTED_SCHEDULE_MISSING'
    assert r['exact_trade_profile'] is False and r['forecast_created'] is False and r['old_records_mutated'] is False
    assert r['source_series_sha256']==hashlib.sha256(before).hexdigest()
    assert before==canonical_bytes(s.to_dict()) and r==report(s)
    body={k:v for k,v in r.items() if k!='report_id'}
    assert r['report_id']=='profile_research_'+hashlib.sha256(canonical_bytes(body)).hexdigest()


@pytest.mark.parametrize('n',[0,1,49])
def test_short_window_not_patched_with_older_or_missing_rows(n):
    r=report(series(n))
    assert r['status']=='INSUFFICIENT_HISTORY' and r['profile'] is None and r['selected_bar_count']==n


@pytest.mark.parametrize('field,value,reason',[
    ('volume',None,'MISSING_OR_INVALID_VOLUME_IN_SELECTED_WINDOW'),
    ('volume',-1.,'MISSING_OR_INVALID_VOLUME_IN_SELECTED_WINDOW'),
    ('volume',True,'MISSING_OR_INVALID_VOLUME_IN_SELECTED_WINDOW'),
    ('completed',False,'UNFINISHED_BAR_IN_SELECTED_WINDOW'),
    ('close',None,'INVALID_OHLC_IN_SELECTED_WINDOW'),
    ('high',0.,'INVALID_OHLC_IN_SELECTED_WINDOW'),
])
def test_bad_selected_row_closes_window_without_compression(field,value,reason):
    s=series();rows=list(s.candles);rows[30]=replace(rows[30],**{field:value})
    r=report(replace(s,candles=tuple(rows)))
    assert r['profile'] is None and r['reason']==reason and r['selected_bar_count']==50


def test_bad_old_volume_outside_selected_window_does_not_affect_result():
    s=series();bad=replace(s,candles=(replace(s.candles[0],volume=None),*s.candles[1:]))
    assert report(bad)['profile']==report(s)['profile']
    assert report(bad)['source_series_sha256']!=report(s)['source_series_sha256']


def test_zero_is_known_zero_not_missing():
    s=series();rows=list(s.candles);rows[-1]=replace(rows[-1],volume=0.)
    r=report(replace(s,candles=tuple(rows)))
    assert r['status']=='AVAILABLE_APPROXIMATE' and r['profile']['total_volume']==4900. and r['selected_bar_count']==50
    allzero=replace(s,candles=tuple(replace(c,volume=0.) for c in s.candles))
    assert report(allzero)['reason']=='NO_POSITIVE_VOLUME_IN_SELECTED_WINDOW'


@pytest.mark.parametrize('caps',[('CANDLES',),('VOLUME',),()])
def test_capability_not_applicable_not_missing_or_zero(caps):
    s=series();s=replace(s,instrument=replace(s.instrument,data_capabilities=caps))
    r=report(s)
    assert r['status']=='NOT_APPLICABLE' and r['profile'] is None


@pytest.mark.parametrize('mode',['future_receipt','future_end','unknown_receipt','naive','duplicate','unordered','wrong_tf','source_after_analysis'])
def test_time_and_identity_fail_closed(mode):
    s=series();kwargs={'analysis_at':ANALYSIS,'source_observed_at':SEEN};rows=list(s.candles)
    if mode=='future_receipt':rows[-1]=replace(rows[-1],observed_at='2026-03-06T00:00:00Z')
    if mode=='future_end':rows[-1]=replace(rows[-1],end='2026-03-06T00:00:00Z')
    if mode=='unknown_receipt':rows[-1]=replace(rows[-1],observed_at=None)
    if mode=='naive':rows[-1]=replace(rows[-1],begin='2026-03-03T11:00:00')
    if mode=='duplicate':rows[-1]=rows[-2]
    if mode=='unordered':rows.reverse()
    if mode=='wrong_tf':s=replace(s,timeframe='D1')
    if mode=='source_after_analysis':kwargs['source_observed_at']='2026-03-06T00:00:00Z'
    s=replace(s,candles=tuple(rows))
    with pytest.raises((ValueError,TypeError)):build_candle_profile_research(s,**kwargs)


@pytest.mark.parametrize('mode',['matches','missing','unexpected'])
def test_declared_schedule_can_close_profile_but_never_attests_calendar(mode):
    s=series();expected=schedule(s)
    if mode=='missing':
        begin=datetime.fromisoformat(s.candles[-1].begin)+timedelta(hours=1)
        end=begin+timedelta(minutes=59,seconds=59)
        expected['window_end']=end.isoformat();expected['expected_bars'].append({'begin':begin.isoformat(),'end':end.isoformat()})
    if mode=='unexpected':expected['expected_bars'].pop(20)
    r=report(s,schedule_bytes=canonical_bytes(expected))
    assert (r['profile'] is not None)==(mode=='matches')
    assert r['calendar_completeness']=='NOT_INDEPENDENTLY_ATTESTED'
    if mode!='matches':assert r['reason']==('MISSING_EXPECTED_BARS' if mode=='missing' else 'UNEXPECTED_BARS')


def test_nonfinite_source_rejected_and_overflow_result_unavailable():
    s=series();rows=list(s.candles);rows[-1]=replace(rows[-1],volume=float('nan'))
    with pytest.raises(ValueError):report(replace(s,candles=tuple(rows)))
    huge=replace(s,candles=tuple(replace(c,volume=1e308) for c in s.candles))
    assert report(huge)['profile'] is None and report(huge)['reason']=='PROFILE_ARITHMETIC_UNAVAILABLE'


def test_readable_text_never_claims_exact_trades_or_trading_direction():
    text=render_candle_profile_research(report())
    assert 'приближение' in text and 'старые прогнозы не изменены' in text


def test_six_original_archive_repeat_readonly_and_full_hash(tmp_path):
    path,sha=archive(tmp_path);before=path.read_bytes();r=compute(path,sha,analysis_at=AT)
    assert len(r['markets'])==6 and r==compute(path,sha,analysis_at=AT) and path.read_bytes()==before
    assert all(row['report']['status']=='AVAILABLE_APPROXIMATE' for row in r['markets'])
    body={k:v for k,v in r.items() if k!='report_id'}
    assert r['report_id']=='six_profiles_'+hashlib.sha256(canonical_bytes(body)).hexdigest()


@pytest.mark.parametrize('mode',['page_sha','duplicate_market','wrong_secid','duplicate_json','traversal'])
def test_bad_archive_refused(tmp_path,mode):
    path,sha=archive(tmp_path,mode)
    with pytest.raises((ValueError,KeyError)):compute(path,sha,analysis_at=AT)


@pytest.mark.parametrize('format',['text','json'])
def test_actual_archive_command(tmp_path,format):
    path,sha=archive(tmp_path)
    r=subprocess.run([sys.executable,'scripts/g2_candle_profile_research.py','--archive',str(path),'--archive-sha256',sha,'--analysis-at',AT,'--format',format],capture_output=True,encoding='utf-8',check=True)
    if format=='json':assert json.loads(r.stdout)==compute(path,sha,analysis_at=AT)
    else:assert 'Исследовательский приблизительный профиль' in r.stdout


@pytest.mark.parametrize('duration',[0,3601,7200])
def test_h1_source_rejects_non_hour_interval(duration):
    s=series();rows=list(s.candles)
    start=datetime.fromisoformat(rows[-1].begin)
    rows[-1]=replace(rows[-1],end=(start+timedelta(seconds=duration)).isoformat())
    with pytest.raises(ValueError,match='H1 bars'):
        report(replace(s,candles=tuple(rows)))


@pytest.mark.parametrize('duration',[1,3599,3600])
def test_shortened_or_exact_hour_completed_bar_is_allowed(duration):
    s=series();rows=list(s.candles)
    start=datetime.fromisoformat(rows[-1].begin)
    rows[-1]=replace(rows[-1],end=(start+timedelta(seconds=duration)).isoformat())
    assert report(replace(s,candles=tuple(rows)))['status']=='AVAILABLE_APPROXIMATE'


@pytest.mark.parametrize('field',['open','high','low','close','volume','value'])
def test_raw_archive_boolean_not_normalized_into_quantity(tmp_path,field):
    import zipfile
    path,_=archive(tmp_path)
    with zipfile.ZipFile(path) as z:
        payloads={name:z.read(name) for name in z.namelist()}
    manifest=json.loads(payloads['manifest.json'])
    page=manifest['instruments'][0]['timeframes']['H1']['pages'][0]
    raw=json.loads(payloads[page['path']]);columns=raw['candles']['columns']
    raw['candles']['data'][-1][columns.index(field)]=True
    payloads[page['path']]=canonical_bytes(raw)
    sha=hashlib.sha256(payloads[page['path']]).hexdigest();page['sha256']=sha
    for item in manifest['all_payload_files']:
        if item['name']==page['path']:item['sha256']=sha
    payloads['manifest.json']=canonical_bytes(manifest)
    with zipfile.ZipFile(path,'w') as z:
        for name,body in payloads.items():z.writestr(name,body)
    with pytest.raises(ValueError,match='boolean raw'):
        compute(path,hashlib.sha256(path.read_bytes()).hexdigest(),analysis_at=AT)


def test_profile_bins_conserve_volume_and_cover_seventy_percent():
    s=series();p=report(s)['profile']
    assert sum(b['volume'] for b in p['bins'])==sum(c.volume for c in s.candles[-50:])
    assert len(p['bins'])==24
    covered=sum(b['volume'] for b in p['bins'] if b['low']>=p['val'] and b['high']<=p['vah'])
    assert covered>=p['total_volume']*.70
    # Independently locate each typical price in the returned intervals.
    expected=[0.]*24
    lo=p['bins'][0]['low'];width=p['bins'][0]['high']-lo
    for c in s.candles[-50:]:
        price=c.high/3+c.low/3+c.close/3
        index=min(23,max(0,int((price-lo)/width)))
        expected[index]+=c.volume
    assert [b['volume'] for b in p['bins']]==expected


def test_invalid_zip_command_has_readable_error_without_traceback(tmp_path):
    path=tmp_path/'invalid.zip';path.write_bytes(b'not a zip')
    result=subprocess.run([sys.executable,'scripts/g2_candle_profile_research.py',
        '--archive',str(path),'--archive-sha256',hashlib.sha256(path.read_bytes()).hexdigest(),
        '--analysis-at',AT],capture_output=True,encoding='utf-8')
    assert result.returncode==2 and 'error:' in result.stderr and 'Traceback' not in result.stderr
