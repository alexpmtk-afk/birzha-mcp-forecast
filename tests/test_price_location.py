from dataclasses import replace
import hashlib
import pytest
from test_production_feature_contracts import snapshot
from test_price_level_evidence import with_profile, exact_profile
from birzha.application.price_location import build_price_location_report, render_price_location_report
from birzha.application.level_reaction import canonical_bytes
from birzha.application.forecast import build_forecast_from_snapshot


def price(snap, value):
    return replace(snap, m15=replace(snap.m15,last_close=value), normalized_features=replace(snap.normalized_features,current_price=round(value,6) if value is not None else snap.h1.last_close))


def test_nearest_preserves_all_tied_origins_and_original_snapshot():
    snap=snapshot(60)
    before=snap.to_dict()
    report=build_price_location_report(snap)
    assert report['nearest_above']['price']==160
    assert report['nearest_below']['price']==139
    assert len(report['nearest_above']['origins'])==3
    assert len(report['nearest_below']['origins'])==3
    assert report['nearest_above']['absolute_distance_price']==1
    assert report['nearest_below']['absolute_distance_price']==20
    assert report['exact_matches']==[]
    assert snap.to_dict()==before
    assert report==build_price_location_report(snap)
    body={k:v for k,v in report.items() if k!='report_id'}
    assert report['report_id']=='location_'+hashlib.sha256(canonical_bytes(body)).hexdigest()
    assert report['location_label'] is None and report['level_strength']=='UNKNOWN'
    assert 'Ближайший уровень выше: 160' in render_price_location_report(report)


@pytest.mark.parametrize('value',[0.,-1.,139.,160.,139.00000000001,1000.])
def test_exact_equality_and_signed_distances_use_raw_price(value):
    report=build_price_location_report(price(snapshot(60),value))
    for item in report['levels'][:6]:
        level=item['origin']['price']
        expected=level-value
        assert item['signed_distance_price']==expected
        assert item['side']==('ON' if level==value else 'ABOVE' if level>value else 'BELOW')
        assert item['signed_distance_pct']==pytest.approx(expected/abs(value)*100) if value else item['signed_distance_pct'] is None
    assert len(report['exact_matches'])==(3 if value in {139.,160.} else 0)
    if value==0:
        assert report['levels'][0]['distance_pct_reason']=='ZERO_REFERENCE_PRICE'


@pytest.mark.parametrize('n',[0,1,14,15,19,20,21,49,50,60])
def test_shortage_gates_price_levels_and_atr_independently(n):
    report=build_price_location_report(snapshot(n))
    assert (report['reference']['price'] is not None)==(n>=1)
    assert (report['nearest_above'] is not None)==(n>=20)
    assert (report['atr_scale']['price_scale'] is not None)==(n>=15)


@pytest.mark.parametrize('mode',['future','date_only','missing_end','bad_end','degraded','duplicate','count','wrong_tf','legacy','norm_conflict','missing_norm'])
def test_bad_selected_reference_never_falls_back(mode):
    snap=snapshot(60)
    qs=snap.quality_contract.timeframes
    changes={'future':{'latest_completed_end':'2026-03-02T18:00:00'},'date_only':{'latest_completed_end':'2026-03-01'},'missing_end':{'latest_completed_end':None},'bad_end':{'latest_completed_end':'bad'},'degraded':{'status':'DEGRADED'},'count':{'candles':59}}
    if mode in changes: qs=tuple(replace(q,**changes[mode]) if q.timeframe=='M15' else q for q in qs)
    if mode=='duplicate': qs=qs+(qs[-1],)
    snap=replace(snap,quality_contract=replace(snap.quality_contract,timeframes=qs))
    if mode=='wrong_tf': snap=replace(snap,m15=replace(snap.m15,timeframe='D1'))
    if mode=='legacy': snap=replace(snap,normalized_features=replace(snap.normalized_features,version='NORMALIZED_FEATURES_V1'))
    if mode=='norm_conflict': snap=replace(snap,normalized_features=replace(snap.normalized_features,current_price=1.))
    if mode=='missing_norm': snap=replace(snap,normalized_features=None)
    report=build_price_location_report(snap)
    assert report['reference']['price'] is None and report['reference']['timeframe']=='M15'
    assert all(i['side'] is None and i['signed_distance_price'] is None for i in report['levels'])
    assert report['exact_matches'] is None
    assert 'недоступно' in render_price_location_report(report)


def test_missing_m15_selects_h1_with_its_own_time():
    report=build_price_location_report(price(snapshot(60),None))
    assert report['reference']['timeframe']=='H1' and report['reference']['price']==159


@pytest.mark.parametrize('mode',['missing','future','short','conflict','zero'])
def test_bad_atr_does_not_erase_absolute_distance(mode):
    snap=snapshot(60)
    if mode=='missing': snap=replace(snap,d1=replace(snap.d1,atr_14_pct=None))
    if mode=='zero': snap=replace(snap,d1=replace(snap.d1,atr_14_pct=0))
    if mode=='future': snap=replace(snap,quality_contract=replace(snap.quality_contract,timeframes=tuple(replace(q,latest_completed_end='2026-03-02T18:00:00') if q.timeframe=='D1' else q for q in snap.quality_contract.timeframes)))
    if mode=='short': snap=replace(snap,d1=replace(snap.d1,candles=14))
    if mode=='conflict': snap=replace(snap,normalized_features=replace(snap.normalized_features,d1_atr_price_scale=99))
    report=build_price_location_report(snap)
    assert report['atr_scale']['price_scale'] is None
    assert report['nearest_above']['absolute_distance_price']==1
    assert report['levels'][2]['signed_distance_atr'] is None
    assert report['levels'][2]['distance_atr_reason']


@pytest.mark.parametrize('factory',[with_profile,exact_profile])
def test_profile_origins_keep_approximation_and_no_invented_location(factory):
    snap=factory()
    report=build_price_location_report(snap)
    assert all(i['side'] is not None for i in report['levels'][-3:])
    assert report['levels'][-1]['status']==('AVAILABLE_APPROXIMATE' if factory==with_profile else 'AVAILABLE')
    record=build_forecast_from_snapshot(snap)
    assert record.location is None
    assert record.to_dict()==build_forecast_from_snapshot(snap).to_dict()


def test_nonfinite_input_rejected_before_content_addressing():
    snap=snapshot(60)
    with pytest.raises(ValueError): build_price_location_report(replace(snap,m15=replace(snap.m15,last_close=float('nan'))))


def test_finite_distance_overflow_is_null_without_losing_order():
    snap=price(snapshot(60),-1e308)
    snap=replace(snap,h1=replace(snap.h1,support_20=1e308,resistance_20=1e308))
    report=build_price_location_report(snap)
    item=report['levels'][2]
    assert item['side']=='ABOVE' and item['signed_distance_price'] is None
    assert item['distance_price_reason']=='ARITHMETIC_OVERFLOW'
    canonical_bytes(report)


def test_price_change_changes_identity_without_mutating_forecast():
    snap=snapshot(60)
    record=build_forecast_from_snapshot(snap).to_dict()
    assert build_price_location_report(snap)['report_id']!=build_price_location_report(price(snap,150))['report_id']
    assert build_forecast_from_snapshot(snap).to_dict()==record


@pytest.mark.parametrize('factory',[snapshot,with_profile,exact_profile])
def test_export_command_roundtrip_and_input_is_unchanged(tmp_path,factory):
    import json
    from scripts.g2_price_location import run, snapshot_from_dict
    snap=factory()
    path=tmp_path/'snapshot.json'
    path.write_text(json.dumps(snap.to_dict()),encoding='utf-8')
    before=path.read_bytes()
    assert snapshot_from_dict(snap.to_dict()).to_dict()==snap.to_dict()
    assert run(path)==build_price_location_report(snap)
    assert path.read_bytes()==before


@pytest.mark.parametrize('mode',['version','extra','missing','duplicate'])
def test_command_rejects_malformed_or_changed_export(tmp_path,mode):
    import json
    from scripts.g2_price_location import run
    data=snapshot().to_dict()
    if mode=='version': data['contract_version']='V1'
    if mode=='extra': data['unexpected']=1
    if mode=='missing': del data['source']
    text=json.dumps(data)
    if mode=='duplicate': text=text.replace('{','{"symbol":"wrong",',1)
    path=tmp_path/'snapshot.json'
    path.write_text(text,encoding='utf-8')
    with pytest.raises((ValueError,TypeError,KeyError)): run(path)
