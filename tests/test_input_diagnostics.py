from dataclasses import replace
import hashlib
import pytest
from test_production_feature_contracts import snapshot
from test_price_level_evidence import with_profile, exact_profile
from test_six_market_report import inputs, report, exported
from test_level_reaction import series,CUTOFF
from birzha.application.input_diagnostics import build_market_input_diagnostics, measure_source_age, explain_input_reason, render_market_input_diagnostics, REASONS_RU
from birzha.application.level_reaction import canonical_bytes
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.six_market_report import render_six_market_report
from scripts.g2_six_market_report import run


def aware_snapshot():
    s=snapshot(60)
    return replace(s,as_of='2026-03-01T18:00:00+03:00',quality_contract=replace(s.quality_contract,timeframes=tuple(replace(q,latest_completed_end='2026-03-01T15:00:00+03:00') for q in s.quality_contract.timeframes)))


@pytest.mark.parametrize('end,t0,seconds',[
    ('2026-03-01T15:00:00+03:00','2026-03-01T18:00:00+03:00',10800.),
    ('2026-03-01T12:00:00Z','2026-03-01T18:00:00+03:00',10800.),
    ('2026-03-01T18:00:00+03:00','2026-03-01T15:00:00Z',0.),
    ('2026-03-06T18:00:00+03:00','2026-03-09T18:00:00+03:00',259200.),
])
def test_age_explicit_offsets_zero_and_weekend_wall_time(end,t0,seconds):
    result=measure_source_age(end,t0)
    assert result['status']=='MEASURED' and result['elapsed_wall_seconds']==seconds
    assert result['elapsed_wall_hours']==seconds/3600
    assert result['stale'] is None and result['trading_session_gap'] is None and result['receipt_age'] is None


@pytest.mark.parametrize('end,t0,reason',[
    (None,'2026-03-01T18:00:00Z','SOURCE_TIME_UNAVAILABLE'),
    ('bad','2026-03-01T18:00:00Z','SOURCE_TIME_UNAVAILABLE'),
    ('2026-03-01T15:00:00','2026-03-01T18:00:00Z','SOURCE_TIMEZONE_UNSPECIFIED'),
    ('2026-03-01T15:00:00Z','2026-03-01T18:00:00','T0_TIMEZONE_UNSPECIFIED'),
    ('2026-03-01T15:00:00Z','bad','T0_TIME_UNAVAILABLE'),
    ('2026-03-01T15:00:00Z',None,'T0_TIME_UNAVAILABLE'),
    ('2026-03-01T19:00:00Z','2026-03-01T18:00:00Z','SOURCE_AFTER_T0'),
])
def test_unproved_or_future_time_has_no_age(end,t0,reason):
    result=measure_source_age(end,t0)
    assert result['status']=='UNAVAILABLE' and result['reason']['code']==reason
    assert result['elapsed_wall_seconds'] is None and result['elapsed_wall_hours'] is None


def test_bound_diagnostics_cover_three_periods_nine_windows_nine_origins():
    s=aware_snapshot();before=canonical_bytes(s.to_dict())
    d=build_market_input_diagnostics(s)
    assert len(d['timeframes'])==3 and sum(len(tf['windows']) for tf in d['timeframes'])==9
    assert all(tf['age']['elapsed_wall_seconds']==10800 for tf in d['timeframes'])
    assert all(w['status']=='AVAILABLE' for tf in d['timeframes'] for w in tf['windows'])
    assert len(d['levels'])==9 and len({i['origin']['name'] for i in d['levels']})==9
    assert all(i['origin']['snapshot_id']==d['snapshot_id'] and i['origin']['secid']==s.secid and i['origin']['t0']==s.as_of for i in d['levels'])
    assert d['snapshot_sha256']==hashlib.sha256(before).hexdigest()
    body={k:v for k,v in d.items() if k!='report_id'}
    assert d['report_id']=='input_diagnostics_'+hashlib.sha256(canonical_bytes(body)).hexdigest()
    assert d==build_market_input_diagnostics(s) and before==canonical_bytes(s.to_dict())


@pytest.mark.parametrize('mode',['count','duplicate','timeframe','missing'])
def test_quality_identity_conflict_does_not_produce_apparent_age(mode):
    s=aware_snapshot();qs=list(s.quality_contract.timeframes)
    if mode=='count':qs[0]=replace(qs[0],candles=59)
    if mode=='duplicate':qs.append(qs[0])
    if mode=='timeframe':s=replace(s,d1=replace(s.d1,timeframe='H1'))
    if mode=='missing':qs=qs[1:]
    s=replace(s,quality_contract=replace(s.quality_contract,timeframes=tuple(qs)))
    tf=build_market_input_diagnostics(s)['timeframes'][0]
    assert tf['quality_binding_matches'] is False and tf['age']['elapsed_wall_seconds'] is None
    assert tf['age']['reason']['code']=='QUALITY_COUNT_OR_TIMEFRAME_CONFLICT'


def test_bad_quality_keeps_measurable_time_distinct_from_admission():
    s=aware_snapshot();s=replace(s,quality_contract=replace(s.quality_contract,reasons=('UNKNOWN',),timeframes=tuple(replace(q,status='DEGRADED') for q in s.quality_contract.timeframes)))
    d=build_market_input_diagnostics(s)
    assert d['timeframes'][0]['age']['status']=='MEASURED'
    assert all(w['reason_explained']['code']=='SOURCE_QUALITY_UNPROVEN' for w in d['timeframes'][0]['windows'])
    assert 'Качество источника' in render_market_input_diagnostics(d)


def test_short_history_reason_is_specific_and_not_missing_data_zero():
    d=build_market_input_diagnostics(snapshot(19))
    w=d['timeframes'][0]['windows'][-1]
    assert w['actual_candles']==19 and w['required_completed_candles']==21
    assert 'требуется 21' in w['reason_explained']['explanation_ru']
    assert d['timeframes'][0]['age']['elapsed_wall_seconds'] is None # naive fixture times not guessed


@pytest.mark.parametrize('code',list(REASONS_RU))
def test_every_current_known_reason_translates_without_losing_code(code):
    result=explain_input_reason(code)
    assert result['known'] and result['code']==code and result['explanation_ru']==REASONS_RU[code]


@pytest.mark.parametrize('code',['NEW_REASON','REQUIRES_0_COMPLETED_CANDLES','REQUIRES_21_COMPLETED_CANDLES_EXTRA','<script>\n|bad'])
def test_unknown_reason_does_not_get_invented_explanation(code):
    result=explain_input_reason(code)
    assert result['code']==code and result['known'] is False
    assert code not in result['explanation_ru']


@pytest.mark.parametrize('factory',[with_profile,exact_profile])
def test_profile_origin_and_precision_are_not_lost(factory):
    d=build_market_input_diagnostics(factory())
    p=d['levels'][-1]
    assert p['origin']['status']==('AVAILABLE_APPROXIMATE' if factory==with_profile else 'AVAILABLE')
    assert p['precision']==('APPROXIMATE' if factory==with_profile else 'DECLARED_METHOD')
    if factory==with_profile:assert 'приблизительный' in render_market_input_diagnostics(d)


def test_duplicate_prices_keep_separate_source_origins():
    s=aware_snapshot();s=replace(s,h1=replace(s.h1,support_20=s.d1.support_20))
    d=build_market_input_diagnostics(s)
    assert d['levels'][0]['origin']['price']==d['levels'][2]['origin']['price']
    assert d['levels'][0]['origin']['name']!=d['levels'][2]['origin']['name']
    assert len(d['levels'])==9


def test_reference_and_scale_conflicts_have_distinct_explanations():
    s=aware_snapshot();s=replace(s,normalized_features=replace(s.normalized_features,current_price=1.,d1_atr_price_scale=1.))
    d=build_market_input_diagnostics(s)
    assert d['reference']['reason_explained']['code']=='NORMALIZED_REFERENCE_CONFLICT'
    assert d['atr_scale']['reason_explained']['code']=='NORMALIZED_ATR_CONFLICT'


def test_source_after_t0_has_no_age_and_admission_refused():
    s=aware_snapshot();s=replace(s,quality_contract=replace(s.quality_contract,timeframes=tuple(replace(q,latest_completed_end='2026-03-01T19:00:00+03:00') for q in s.quality_contract.timeframes)))
    d=build_market_input_diagnostics(s)
    assert d['timeframes'][0]['age']['elapsed_wall_seconds'] is None
    assert d['timeframes'][0]['age']['reason']['code']=='SOURCE_AFTER_T0'
    assert d['timeframes'][0]['windows'][0]['status']=='UNAVAILABLE'


def test_six_market_diagnostics_have_54_original_levels_and_same_child_ids():
    r=report()
    assert r['version']=='SIX_MARKET_FACTS_V2'
    assert sum(len(row['input_diagnostics']['levels']) for row in r['rows'])==54
    for row in r['rows']:
        d=row['input_diagnostics'];child=row['detail_report']
        assert d['snapshot_sha256']==child['snapshot_sha256']
        assert d['movement_report_id']==child['movement_at_t0']['report_id']
        assert d['location_report_id']==child['level_report']['at_t0']['report_id']
    text=render_six_market_report(r)
    assert text.count('### Происхождение девяти уровней')==6
    assert text.count('### Давность данных')==6


def test_absent_market_keeps_diagnostics_null():
    r=report({})
    assert all(row['input_diagnostics'] is None for row in r['rows'])


def test_later_prices_do_not_change_frozen_input_diagnostics():
    data=inputs();s=series();s=replace(s,instrument=replace(s.instrument,secid=data['SBER']['snapshot'].secid))
    a=report(data)['rows'][0]['input_diagnostics']
    data['SBER'].update(series=s,observation_at=CUTOFF)
    b=report(data)['rows'][0]['input_diagnostics']
    assert a==b


def test_file_runner_keeps_source_bytes_and_renders_new_sections(tmp_path):
    manifest=exported(tmp_path);before={p:p.read_bytes() for p in tmp_path.rglob('*.json')}
    r=run(manifest)
    assert 'Причины недоступности расчётов' in render_six_market_report(r)
    assert before=={p:p.read_bytes() for p in tmp_path.rglob('*.json')}


def test_old_six_market_view_does_not_invent_new_diagnostics():
    r=report();r['version']='SIX_MARKET_FACTS_V1'
    for row in r['rows']:row.pop('input_diagnostics')
    assert 'в этой версии сводки нет' in render_six_market_report(r)
