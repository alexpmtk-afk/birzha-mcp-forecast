from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
from test_production_feature_contracts import snapshot
from test_level_reaction import series, CUTOFF
from test_level_holding import schedule
from test_price_level_evidence import with_profile, exact_profile
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.linked_level_report import build_linked_level_report, render_linked_level_report
from birzha.application.level_reaction import canonical_bytes
from scripts.g2_linked_level_report import run


def inputs():
    snap=snapshot(60)
    snap=replace(snap,d1=replace(snap.d1,support_20=100.,resistance_20=110.))
    return snap,build_forecast_from_snapshot(snap).to_dict()


def report(*,s=None,calendar=True):
    snap,forecast=inputs()
    s=series() if s is None else s
    return build_linked_level_report(snap,forecast,series=s,observation_at=CUTOFF,schedule_bytes=canonical_bytes(schedule(s)) if calendar else None)


def test_linked_facts_keep_t0_and_future_separate_without_mutation():
    snap,forecast=inputs();s=series()
    before=(canonical_bytes(snap.to_dict()),canonical_bytes(forecast),canonical_bytes(s.to_dict()))
    linked=build_linked_level_report(snap,forecast,series=s,observation_at=CUTOFF,schedule_bytes=canonical_bytes(schedule(s)))
    assert linked['at_t0']['reference']['price']==159
    assert linked['rows'][0]['at_t0']['side']=='BELOW' # level100 below T0 price159
    assert linked['rows'][0]['after_t0']['range_contact_bars']==4
    assert linked['rows'][0]['after_t0']['close_counts']=={'ABOVE':2,'ON':1,'BELOW':1}
    assert linked['rows'][0]['after_t0']['longest_close_runs']=={'ABOVE':1,'ON':1,'BELOW':1}
    assert linked['after_t0']['reaction']['level_fixed_at']==snap.as_of
    assert linked['after_t0']['holding']['reaction_report_id']==linked['after_t0']['reaction']['report_id']
    assert linked['forecast_mutated'] is False and linked['predictive_quality']=='NOT_MEASURED'
    assert linked['full_location'] is None and linked['control']=='UNKNOWN'
    assert before==(canonical_bytes(snap.to_dict()),canonical_bytes(forecast),canonical_bytes(s.to_dict()))


def test_t0_only_is_useful_and_does_not_invent_observations():
    snap,forecast=inputs()
    linked=build_linked_level_report(snap,forecast)
    assert linked['after_t0'] is None
    assert all(row['after_t0'] is None for row in linked['rows'])
    text=render_linked_level_report(linked)
    assert 'Последующие свечи не переданы' in text and 'Опорная цена: 159.0' in text


@pytest.mark.parametrize('field,value',[
    ('snapshot_id','other'),('symbol','OTHER'),('secid','OTHER'),
    ('created_at_t0','2026-03-02T18:00:00'),('snapshot_contract_version','OLD'),
    ('reference_price',150.),('reference_price',True),('forecast_id',''),
    ('record_version','FORECAST_RECORD_V1'),
])
def test_mismatched_forecast_refused(field,value):
    snap,forecast=inputs();forecast[field]=value
    with pytest.raises(ValueError):build_linked_level_report(snap,forecast)


def test_forged_but_internally_consistent_level_origins_refused():
    snap,forecast=inputs()
    forecast['level_evidence'][0]['price']=99.
    forecast['key_levels']=sorted({i['price'] for i in forecast['level_evidence'] if i['price'] is not None})
    with pytest.raises(ValueError,match='origins'):build_linked_level_report(snap,forecast)


def test_changed_snapshot_under_old_identity_refused():
    snap,forecast=inputs()
    with pytest.raises(ValueError,match='identity'):
        build_linked_level_report(replace(snap,source='OTHER'),forecast)


@pytest.mark.parametrize('mode',['no_cutoff','cutoff_without_series','schedule_without_series','wrong_instrument','future_receipt','unfinished'])
def test_invalid_observation_binding_refused(mode):
    snap,forecast=inputs();s=series();kwargs={'series':s,'observation_at':CUTOFF}
    if mode=='no_cutoff':kwargs.pop('observation_at')
    if mode=='cutoff_without_series':kwargs.pop('series')
    if mode=='schedule_without_series':kwargs={'schedule_bytes':canonical_bytes(schedule())}
    if mode=='wrong_instrument':kwargs['series']=replace(s,instrument=replace(s.instrument,secid='OTHER'))
    if mode=='future_receipt':kwargs['series']=replace(s,candles=(replace(s.candles[0],observed_at='2026-03-03T10:00:00+03:00'),*s.candles[1:]))
    if mode=='unfinished':kwargs['series']=replace(s,candles=(replace(s.candles[0],completed=False),*s.candles[1:]))
    with pytest.raises(ValueError):build_linked_level_report(snap,forecast,**kwargs)


@pytest.mark.parametrize('mode',['no_schedule','missing_middle','unexpected'])
def test_reaction_counts_remain_supplied_only_and_runs_close_on_gaps(mode):
    snap,forecast=inputs();s=series();expected=schedule(s)
    if mode=='missing_middle':s=replace(s,candles=(s.candles[0],s.candles[2],s.candles[3]))
    if mode=='unexpected':expected['expected_bars']=expected['expected_bars'][:-1]
    linked=build_linked_level_report(snap,forecast,series=s,observation_at=CUTOFF,schedule_bytes=None if mode=='no_schedule' else canonical_bytes(expected))
    first=linked['rows'][0]['after_t0']
    assert first['range_contact_bars']==len(s.candles)
    assert first['longest_close_runs'] is None and first['holding_reason']
    text=render_linked_level_report(linked)
    assert 'серии закрытий недоступны' in text


@pytest.mark.parametrize('factory',[with_profile,exact_profile])
def test_original_profile_precision_survives_join(factory):
    snap=factory();forecast=build_forecast_from_snapshot(snap).to_dict()
    linked=build_linked_level_report(snap,forecast)
    assert linked['rows'][-1]['origin']['status']==('AVAILABLE_APPROXIMATE' if factory==with_profile else 'AVAILABLE')
    if factory==with_profile:assert 'приблизительный' in render_linked_level_report(linked)


@pytest.mark.parametrize('n',[0,1,14,19,20,21,49,50])
def test_short_history_remains_explicit_and_no_fabricated_zeros(n):
    snap=snapshot(n);forecast=build_forecast_from_snapshot(snap).to_dict()
    linked=build_linked_level_report(snap,forecast)
    assert (linked['rows'][0]['origin']['price'] is None)==(n<20)
    assert linked['rows'][0]['after_t0'] is None


def test_future_prices_only_change_observations_not_t0():
    first=report(s=series((101.,99.,100.,101.)))
    second=report(s=series((120.,121.,122.,123.)))
    assert first['at_t0']==second['at_t0']
    assert first['forecast_sha256']==second['forecast_sha256']
    assert first['report_id']!=second['report_id']
    assert first['after_t0']!=second['after_t0']


def test_report_content_identity_is_stable_and_complete():
    linked=report()
    body={k:v for k,v in linked.items() if k!='report_id'}
    assert linked['report_id']=='linked_levels_'+hashlib.sha256(canonical_bytes(body)).hexdigest()
    assert linked==report()
    assert 'Эти данные не были входом прежнего прогноза' in render_linked_level_report(linked)


def exported(tmp_path):
    snap,forecast=inputs();s=series()
    payloads={'snapshot':snap.to_dict(),'forecast':forecast,'candles':s.to_dict(),'schedule':schedule(s)}
    paths={name:tmp_path/(name+'.json') for name in payloads}
    for name,path in paths.items():path.write_bytes(canonical_bytes(payloads[name]))
    return paths


def test_file_runner_reads_only_and_preserves_every_source(tmp_path):
    paths=exported(tmp_path);before={name:p.read_bytes() for name,p in paths.items()}
    linked=run(paths['snapshot'],paths['forecast'],candles_path=paths['candles'],schedule_path=paths['schedule'],observation_at=CUTOFF)
    assert linked==report()
    assert before=={name:p.read_bytes() for name,p in paths.items()}


@pytest.mark.parametrize('mode',['duplicate','count'])
def test_runner_refuses_ambiguous_exports(tmp_path,mode):
    paths=exported(tmp_path)
    text=paths['candles'].read_text(encoding='utf-8')
    if mode=='duplicate':text=text.replace('{','{"count":999,',1)
    else:
        data=json.loads(text);data['count']=999;text=json.dumps(data)
    paths['candles'].write_text(text,encoding='utf-8')
    with pytest.raises(ValueError):run(paths['snapshot'],paths['forecast'],candles_path=paths['candles'],observation_at=CUTOFF)


@pytest.mark.parametrize('format',['text','json'])
def test_actual_command_prints_linked_report(tmp_path,format):
    paths=exported(tmp_path)
    completed=subprocess.run([sys.executable,'scripts/g2_linked_level_report.py','--snapshot',str(paths['snapshot']),'--forecast',str(paths['forecast']),'--candles',str(paths['candles']),'--schedule',str(paths['schedule']),'--observation-at',CUTOFF,'--format',format],capture_output=True,encoding='utf-8',check=True)
    if format=='json':assert json.loads(completed.stdout)['version']=='LINKED_LEVEL_FACTS_V1'
    else:assert '## При записи прогноза' in completed.stdout and '## После записи прогноза' in completed.stdout
