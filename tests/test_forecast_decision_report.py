from copy import deepcopy
from dataclasses import replace
import hashlib
import pytest
from test_production_feature_contracts import snapshot
from test_linked_level_report import inputs, exported
from test_level_reaction import series, CUTOFF
from birzha.application.forecast import build_forecast_from_snapshot, ForecastParameters
from birzha.application.forecast_decision_report import build_forecast_decision_report, render_forecast_decision_report, explain_abstention_reason
from birzha.application.combined_market_report import build_combined_market_report, render_combined_market_report
from birzha.application.level_reaction import canonical_bytes
from scripts.g2_combined_market_report import run


def payload(n=60):
    return build_forecast_from_snapshot(snapshot(n)).to_dict()


@pytest.mark.parametrize('n',[0,1,14,19,20])
def test_abstention_never_reads_as_neutral_prediction(n):
    record = payload(n); before = canonical_bytes(record)
    report = build_forecast_decision_report(record)
    assert report['decision_status'] == 'ABSTAIN' and report['direction'] == 'NEUTRAL'
    assert [item['code'] for item in report['abstention_reasons']] == record['abstention_reasons']
    assert report['abstention_reasons'] and all(item['known'] for item in report['abstention_reasons'])
    assert report['horizons'] == record['horizons']
    assert 'Это не прогноз бокового движения' in render_forecast_decision_report(report)
    assert before == canonical_bytes(record)


def test_neutral_is_not_missing_data_refusal():
    snap = snapshot(60)
    snap = replace(snap,**{tf:replace(getattr(snap,tf),trend_score=0.,return_5=0.) for tf in ('d1','h1','m15')})
    report = build_forecast_decision_report(build_forecast_from_snapshot(snap).to_dict())
    assert report['decision_status'] == 'BASELINE_NEUTRAL' and report['abstention_reasons'] == []
    assert 'выбрана нейтральная оценка' in render_forecast_decision_report(report)


@pytest.mark.parametrize('params,direction',[(ForecastParameters(),'UP'),(ForecastParameters(d1_weight=-1.,h1_weight=-1.,m15_weight=-1.,alignment_weight=0.),'DOWN')])
def test_directional_estimate_not_probability(params,direction):
    record = build_forecast_from_snapshot(snapshot(60),parameters=params).to_dict()
    report = build_forecast_decision_report(record)
    assert report['direction'] == direction and report['decision_status'] == 'BASELINE_DIRECTIONAL_ESTIMATE'
    assert report['signal_strength'] == record['signal_strength']
    assert report['probability'] is None and report['predictive_quality'] == 'NOT_MEASURED'
    assert 'Это не вероятность успеха' in render_forecast_decision_report(report)


@pytest.mark.parametrize('code,text',[
    ('DATA_QUALITY_CONTRACT_MISSING','качество исходных'),
    ('CAUSAL_REFERENCE_PRICE_UNAVAILABLE','опорной цены'),
    ('D1_POSITIVE_ATR_PRICE_SCALE_UNAVAILABLE','масштаба колебаний'),
    ('D1_RAW_ATR_INPUT_UNAVAILABLE','дневной цены'),
    ('D1_BASELINE_SCORE_UNAVAILABLE','оценки направления'),
    ('D1_QUALITY_COUNT_CONFLICT','Число дневных свечей'),
    ('d1_return_20_atr:INSUFFICIENT_HISTORY:D1_requires_21_candles','требуется 21'),
    ('d1_efficiency_20:UNAVAILABLE:D1_quality_not_proven_for_feature','качество дневных'),
    ('d1_return_5_atr:UNAVAILABLE:causal_value_not_available_at_snapshot_T0','значение недоступно'),
    ('d1_atr_price_scale:UNAVAILABLE:nonfinite_or_invalid_numeric_value','конечным числом'),
])
def test_known_reasons_have_concrete_russian_meaning(code,text):
    result = explain_abstention_reason(code)
    assert result['known'] and result['code'] == code and text in result['explanation_ru']


@pytest.mark.parametrize('code',['NEW_REASON','d1_return_20_atr:UNAVAILABLE:UNKNOWN','d1_return_20_atr:AVAILABLE:D1_requires_21_candles','d1_return_20_atr:INSUFFICIENT_HISTORY:D1_requires_0_candles','unknown:UNAVAILABLE:causal_value_not_available_at_snapshot_T0','<script>\n|#bad'])
def test_unknown_reason_preserved_without_inventing_meaning(code):
    record = payload(19); record['abstention_reasons'] = [code]
    report = build_forecast_decision_report(record)
    assert report['abstention_reasons'][0]['code'] == code
    assert report['abstention_reasons'][0]['known'] is False
    assert code not in render_forecast_decision_report(report)


def test_optional_exclusion_does_not_invent_whole_refusal_or_reason():
    snap = snapshot(60)
    snap = replace(snap,normalized_features=replace(snap.normalized_features,h1_return_5_atr=None))
    record = build_forecast_from_snapshot(snap).to_dict()
    report = build_forecast_decision_report(record)
    assert report['decision_status'] != 'ABSTAIN'
    assert report['excluded_components'][0]['component'] == 'H1'
    assert 'не уточняет отдельную причину' in report['excluded_components'][0]['explanation_ru']
    assert 'само по себе не означает отказ' in render_forecast_decision_report(report)


def test_profile_exclusion_and_raw_diagnostics_are_preserved():
    record = payload(); record['reasons'] += ['optional_profile_excluded','NEW_DIAGNOSTIC']
    record['warnings'] += ['NEW_WARNING']
    report = build_forecast_decision_report(record)
    assert report['excluded_components'][-1]['component'] == 'PROFILE'
    assert report['source_reasons'] == record['reasons'] and report['source_warnings'] == record['warnings']


@pytest.mark.parametrize('field,value',[
    ('decision_status','ABSTAIN'),('abstention_reasons',['unexpected']),('direction','NEUTRAL'),
    ('field_availability',{}),('engine_version',''),('forecast_id',''),
    ('record_version','FORECAST_RECORD_V1_PROTOCOL_08'),('reasons','text'),('warnings',[None]),
])
def test_semantically_conflicting_or_incomplete_record_refused(field,value):
    record = payload(); record[field] = value
    with pytest.raises(ValueError):build_forecast_decision_report(record)


def test_hash_and_no_shared_mutable_source_containers():
    record = payload(19); before = canonical_bytes(record)
    report = build_forecast_decision_report(record)
    body = {k:v for k,v in report.items() if k != 'report_id'}
    assert report['report_id'] == 'frozen_decision_' + hashlib.sha256(canonical_bytes(body)).hexdigest()
    assert report['forecast_sha256'] == hashlib.sha256(before).hexdigest()
    assert report == build_forecast_decision_report(record)
    report['horizons'][0]['sessions'] = 99
    report['field_availability']['directional_estimate'] = 'ALTERED'
    report['source_reasons'].append('ALTERED')
    assert before == canonical_bytes(record)


def test_combined_version_binding_and_future_isolation():
    snap, record = inputs()
    a = build_combined_market_report(snap,record,series=series(),observation_at=CUTOFF)
    b = build_combined_market_report(snap,record,series=series((120.,121.,122.,123.)),observation_at=CUTOFF)
    assert a['version'] == 'COMBINED_MARKET_FACTS_V2'
    assert a['decision_at_t0'] == b['decision_at_t0']
    assert a['decision_at_t0']['forecast_sha256'] == a['forecast_sha256'] == a['level_report']['forecast_sha256']
    text = render_combined_market_report(a)
    assert text.index('## Исходное решение') < text.index('## Сравнение движения') < text.index('### После записи')


def test_original_decision_is_not_rebuilt_from_current_default_parameters():
    snap, _ = inputs()
    record = build_forecast_from_snapshot(snap,parameters=ForecastParameters(direction_threshold=1000.)).to_dict()
    assert record['decision_status'] == 'BASELINE_NEUTRAL'
    assert build_forecast_from_snapshot(snap).decision_status == 'BASELINE_DIRECTIONAL_ESTIMATE'
    report = build_combined_market_report(snap,record)
    assert report['decision_at_t0']['decision_status'] == 'BASELINE_NEUTRAL'
    assert report['forecast_sha256'] == hashlib.sha256(canonical_bytes(record)).hexdigest()


def test_file_runner_preserves_forecast_bytes_and_adds_decision(tmp_path):
    paths = exported(tmp_path); before = {k:p.read_bytes() for k,p in paths.items()}
    report = run(paths['snapshot'],paths['forecast'],candles_path=paths['candles'],observation_at=CUTOFF,schedule_path=paths['schedule'])
    assert report['decision_at_t0']['decision_status'] == 'BASELINE_DIRECTIONAL_ESTIMATE'
    assert before == {k:p.read_bytes() for k,p in paths.items()}


def test_old_combined_view_does_not_invent_missing_decision():
    snap, record = inputs(); old = build_combined_market_report(snap,record)
    old.pop('decision_at_t0'); old['version'] = 'COMBINED_MARKET_FACTS_V1'
    assert 'старой версии отчёта не представлено' in render_combined_market_report(old)
