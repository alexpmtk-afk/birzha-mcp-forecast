from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import subprocess
import sys
import pytest
from test_production_feature_contracts import snapshot
from birzha.application.timeframe_movement import build_timeframe_movement_report, render_timeframe_movement_report
from birzha.application.level_reaction import canonical_bytes
from birzha.application.forecast import build_forecast_from_snapshot
from scripts.g2_timeframe_movement import run


def signs(values, steps=5):
    snap=snapshot(60)
    return replace(snap,**{tf:replace(getattr(snap,tf),**{f'return_{steps}':v}) for tf,v in zip(('d1','h1','m15'),values)})


@pytest.mark.parametrize('values,relations',[
    ((.1,.2,.3),('SAME_SIGN','SAME_SIGN','SAME_SIGN')),
    ((-.1,-.2,-.3),('SAME_SIGN','SAME_SIGN','SAME_SIGN')),
    ((.1,-.2,.3),('OPPOSITE_SIGNS','SAME_SIGN','OPPOSITE_SIGNS')),
    ((0.,0.,0.),('SAME_SIGN','SAME_SIGN','SAME_SIGN')),
    ((0.,.2,-.3),('ONE_EXACTLY_FLAT','ONE_EXACTLY_FLAT','OPPOSITE_SIGNS')),
    ((1e-14,-1e-14,0.),('OPPOSITE_SIGNS','ONE_EXACTLY_FLAT','ONE_EXACTLY_FLAT')),
])
def test_pair_signs_exact_zero_and_complete_summary(values,relations):
    window=build_timeframe_movement_report(signs(values))['windows'][0]
    assert tuple(p['relation'] for p in window['pairs'])==relations
    assert window['complete_direction_counts']=={key:sum(('UP' if v>0 else 'DOWN' if v<0 else 'EXACTLY_FLAT')==key for v in values) for key in ('UP','DOWN','EXACTLY_FLAT')}
    assert window['all_exactly_flat']==all(v==0 for v in values)


@pytest.mark.parametrize('n',[0,1,5,6,10,11,20,21,49,50])
def test_each_window_has_its_actual_minimum(n):
    result=build_timeframe_movement_report(snapshot(n))
    for w in result['windows']:
        assert w['all_timeframes_available']==(n>=w['steps']+1)
        assert all(i['required_completed_candles']==w['steps']+1 for i in w['metrics'])


@pytest.mark.parametrize('mode',['future','unknown_end','bad_end','count','duplicate','degraded','wrong_tf','legacy','missing_quality'])
def test_bad_hour_only_closes_affected_pairs(mode):
    snap=snapshot(60);qs=snap.quality_contract.timeframes
    changes={'future':{'latest_completed_end':'2026-03-02T18:00:00'},'unknown_end':{'latest_completed_end':None},'bad_end':{'latest_completed_end':'bad'},'count':{'candles':59},'degraded':{'status':'DEGRADED'}}
    if mode in changes:qs=tuple(replace(q,**changes[mode]) if q.timeframe=='H1' else q for q in qs)
    if mode=='duplicate':qs=qs+(qs[1],)
    snap=replace(snap,quality_contract=replace(snap.quality_contract,timeframes=qs))
    if mode=='wrong_tf':snap=replace(snap,h1=replace(snap.h1,timeframe='D1'))
    if mode=='legacy':snap=replace(snap,normalized_features=replace(snap.normalized_features,version='OLD'))
    if mode=='missing_quality':snap=replace(snap,quality_contract=None)
    w=build_timeframe_movement_report(snap)['windows'][0]
    assert not w['all_timeframes_available']
    assert w['complete_direction_counts'] is None and w['all_same_nonzero_direction'] is None
    if mode not in ('legacy','missing_quality'):
        assert w['pairs'][1]['status']=='AVAILABLE'
        assert w['pairs'][0]['status']==w['pairs'][2]['status']=='UNAVAILABLE'


@pytest.mark.parametrize('value',[None,True,-1.,-2.])
def test_bad_or_nonpositive_implied_base_does_not_become_flat(value):
    w=build_timeframe_movement_report(signs((.1,value,.2)))['windows'][0]
    assert w['metrics'][1]['direction'] is None
    assert w['complete_direction_counts'] is None
    assert w['pairs'][1]['relation']=='SAME_SIGN'


@pytest.mark.parametrize('close',[0.,-1.,None])
def test_nonpositive_last_close_is_explicit_direction_limitation(close):
    snap=snapshot(60);snap=replace(snap,h1=replace(snap.h1,last_close=close))
    w=build_timeframe_movement_report(snap)['windows'][0]
    assert w['metrics'][1]['last_close']==close
    assert w['metrics'][1]['reason']=='POSITIVE_LAST_CLOSE_REQUIRED_FOR_RETURN_DIRECTION'
    assert w['metrics'][1]['direction'] is None


def test_missing_long_return_does_not_erase_short_window():
    snap=snapshot(60);snap=replace(snap,d1=replace(snap.d1,return_20=None))
    r=build_timeframe_movement_report(snap)
    assert r['windows'][0]['all_timeframes_available']
    assert not r['windows'][2]['all_timeframes_available']


def test_identity_immutability_and_no_alignment_score():
    snap=snapshot(60);before=canonical_bytes(snap.to_dict())
    r=build_timeframe_movement_report(snap)
    assert r==build_timeframe_movement_report(snap)
    body={k:v for k,v in r.items() if k!='report_id'}
    assert r['report_id']=='movement_'+hashlib.sha256(canonical_bytes(body)).hexdigest()
    assert r['alignment_score'] is None and r['full_alignment'] is None
    assert canonical_bytes(snap.to_dict())==before
    assert 'охватывают разное время' in render_timeframe_movement_report(r)


def test_file_runner_binds_original_record_and_does_not_write(tmp_path):
    snap=snapshot(60);record=build_forecast_from_snapshot(snap).to_dict()
    sp=tmp_path/'snapshot.json';fp=tmp_path/'forecast.json'
    sp.write_bytes(canonical_bytes(snap.to_dict()));fp.write_bytes(canonical_bytes(record))
    before=sp.read_bytes(),fp.read_bytes()
    result=run(sp,forecast_path=fp)
    assert result['levels']['snapshot_id']==result['movement']['snapshot_id']
    assert (sp.read_bytes(),fp.read_bytes())==before
    record['snapshot_id']='OTHER';fp.write_bytes(canonical_bytes(record))
    with pytest.raises(ValueError):run(sp,forecast_path=fp)


@pytest.mark.parametrize('format',['text','json'])
def test_real_command_prints_report(tmp_path,format):
    p=tmp_path/'snapshot.json';p.write_bytes(canonical_bytes(snapshot().to_dict()))
    cp=subprocess.run([sys.executable,'scripts/g2_timeframe_movement.py','--snapshot',str(p),'--format',format],check=True,capture_output=True,encoding='utf-8')
    if format=='json':assert json.loads(cp.stdout)['movement']['version']=='TIMEFRAME_MOVEMENT_FACTS_V1'
    else:assert 'Сравнение движения цены' in cp.stdout



def test_unknown_snapshot_version_closes_all_metrics():
    r=build_timeframe_movement_report(replace(snapshot(60),contract_version='OLD'))
    assert all(not w['all_timeframes_available'] for w in r['windows'])
    assert 'версия исходного снимка' in render_timeframe_movement_report(r)


def test_invalid_numeric_source_time_has_readable_unavailable_output():
    snap=snapshot(60)
    qs=tuple(replace(q,latest_completed_end=123) if q.timeframe=='H1' else q for q in snap.quality_contract.timeframes)
    r=build_timeframe_movement_report(replace(snap,quality_contract=replace(snap.quality_contract,timeframes=qs)))
    assert 'время завершения не подтверждено' in render_timeframe_movement_report(r)
