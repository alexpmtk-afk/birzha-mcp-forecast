from dataclasses import replace
import hashlib
import json
import subprocess
import sys
import pytest
from test_production_feature_contracts import snapshot
from test_level_reaction import series, CUTOFF
from test_level_holding import schedule
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.level_reaction import canonical_bytes
from birzha.application.six_market_report import build_six_market_report, render_six_market_report, MARKETS
from scripts.g2_six_market_report import run


def inputs(n=60):
    result = {}
    for market in MARKETS:
        snap = replace(snapshot(n),symbol=market,secid=market+'-EXACT')
        result[market] = {'snapshot':snap,'forecast_payload':build_forecast_from_snapshot(snap).to_dict()}
    return result


def report(data=None):
    return build_six_market_report(inputs() if data is None else data,evidence_mode='OFFLINE_RESEARCH_REPEAT')


def test_all_six_counts_and_decisions_from_bound_original_sources():
    data = inputs(); original = {m:canonical_bytes(v['forecast_payload']) for m,v in data.items()}
    summary = report(data)
    assert [r['market'] for r in summary['rows']] == list(MARKETS)
    assert summary['all_six_markets_supplied'] and summary['supplied_market_count'] == 6
    assert summary['common_t0'] == data['SBER']['snapshot'].as_of
    for row in summary['rows']:
        source = data[row['market']]
        assert row['secid'] == source['snapshot'].secid
        assert [tf['actual_candles'] for tf in row['timeframes']] == [60,60,60]
        assert row['decision_status'] == source['forecast_payload']['decision_status']
        assert row['level_status_counts'] and sum(row['level_status_counts'].values()) == 9
        assert row['later_observation']['supplied_bar_count'] is None
        assert row['detail_report']['forecast_sha256'] == hashlib.sha256(original[row['market']]).hexdigest()
    assert original == {m:canonical_bytes(v['forecast_payload']) for m,v in data.items()}


@pytest.mark.parametrize('count',[0,1,5])
def test_missing_market_never_becomes_zero_or_false_refusal(count):
    data = dict(list(inputs().items())[:count]); summary = report(data)
    assert summary['supplied_market_count'] == count and summary['all_six_markets_supplied'] is False
    assert summary['common_t0'] is None
    for row in summary['rows'][count:]:
        assert row['input_status'] == 'NOT_SUPPLIED'
        for field in ('timeframes','decision_status','direction','abstention_reasons','reference','level_status_counts','later_observation','detail_report'):
            assert row[field] is None
    assert 'не доказательство отсутствия данных на бирже' in render_six_market_report(summary)


def test_short_history_is_actual_refusal_not_missing_market():
    summary = report(inputs(19)); row = summary['rows'][0]
    assert row['input_status'] == 'SUPPLIED_AND_CONTENT_BOUND' and row['decision_status'] == 'ABSTAIN'
    assert row['timeframes'][0]['actual_candles'] == 19
    assert row['timeframes'][0]['windows'][-1]['status'] == 'INSUFFICIENT_HISTORY'
    text = render_six_market_report(summary)
    assert 'отказ от направления' in text and 'мало свечей; нужно 21' in text


def test_available_counts_do_not_claim_quality_admission():
    data = inputs(); snap = data['SBER']['snapshot']
    snap = replace(snap,quality_contract=replace(snap.quality_contract,reasons=('UNKNOWN_DEFECT',),timeframes=tuple(replace(q,status='DEGRADED') for q in snap.quality_contract.timeframes)))
    data['SBER'] = {'snapshot':snap,'forecast_payload':build_forecast_from_snapshot(snap).to_dict()}
    row = report(data)['rows'][0]
    assert row['timeframes'][0]['actual_candles'] == 60
    assert row['timeframes'][0]['windows'][0]['status'] == 'UNAVAILABLE'
    assert row['decision_status'] == 'ABSTAIN'


def test_different_record_times_not_collapsed_to_one_t0():
    data = inputs(); snap = replace(data['Si']['snapshot'],as_of='2026-03-01T18:01:00+03:00')
    data['Si'] = {'snapshot':snap,'forecast_payload':build_forecast_from_snapshot(snap).to_dict()}
    summary = report(data)
    assert summary['common_t0'] is None and summary['all_six_markets_supplied']
    assert summary['rows'][1]['t0'] == snap.as_of


@pytest.mark.parametrize('mode',['wrong_key','wrong_forecast','unknown_market','ready_report','evidence_mode'])
def test_malformed_supplied_input_is_not_silently_missing(mode):
    data = inputs(); kwargs = {'evidence_mode':'OFFLINE_RESEARCH_REPEAT'}
    if mode == 'wrong_key': data['Si'] = data['SBER']
    if mode == 'wrong_forecast':data['SBER']['forecast_payload']['snapshot_id'] = 'wrong'
    if mode == 'unknown_market':data['OTHER'] = data['SBER']
    if mode == 'ready_report':data['SBER'] = {'report':report()['rows'][0]['detail_report']}
    if mode == 'evidence_mode':kwargs['evidence_mode'] = 'REAL_PROSPECTIVE_ISSUANCE'
    with pytest.raises((ValueError,TypeError)):build_six_market_report(data,**kwargs)


@pytest.mark.parametrize('calendar,gap',[(False,False),(True,False),(True,True)])
def test_late_observations_separate_from_source_and_missing_calendar(calendar,gap):
    data = inputs(); s = series(); snap = data['SBER']['snapshot']
    s = replace(s,instrument=replace(s.instrument,symbol=snap.symbol,secid=snap.secid))
    expected = schedule(s)
    if gap:s = replace(s,candles=(s.candles[0],*s.candles[2:]))
    before = report(data)['rows'][0]
    data['SBER'].update(series=s,observation_at=CUTOFF,schedule_bytes=canonical_bytes(expected) if calendar else None)
    after = report(data)['rows'][0]
    for key in ('timeframes','decision_status','direction','abstention_reasons','reference'):
        assert before[key] == after[key]
    later = after['later_observation']
    assert later['supplied_bar_count'] == len(s.candles) and later['status'] == 'SUPPLIED_BARS_ONLY'
    assert (later['available_holding_levels'] > 0) == (calendar and not gap)
    assert later['schedule_continuity']['reason'] == ('EXPECTED_SCHEDULE_MISSING' if not calendar else 'MISSING_EXPECTED_BARS' if gap else None)


def test_report_content_hash_and_order_are_deterministic():
    summary = report()
    body = {k:v for k,v in summary.items() if k != 'report_id'}
    assert summary['report_id'] == 'six_market_' + hashlib.sha256(canonical_bytes(body)).hexdigest()
    assert summary == report(dict(reversed(list(inputs().items()))))
    assert summary['portfolio_direction'] is None and summary['predictive_quality'] == 'NOT_MEASURED'
    assert summary['current_quotes_claimed'] is False


def exported(tmp_path):
    entries=[]
    for market,value in inputs().items():
        sub=tmp_path/market;sub.mkdir()
        (sub/'snapshot.json').write_bytes(canonical_bytes(value['snapshot'].to_dict()))
        (sub/'forecast.json').write_bytes(canonical_bytes(value['forecast_payload']))
        entries.append({'market':market,'snapshot':market+'/snapshot.json','forecast':market+'/forecast.json'})
    manifest=tmp_path/'manifest.json'
    manifest.write_bytes(canonical_bytes({'version':'SIX_MARKET_REPORT_INPUTS_V1','evidence_mode':'OFFLINE_RESEARCH_REPEAT','markets':entries}))
    return manifest


def test_file_runner_reads_only_and_is_portable(tmp_path):
    manifest=exported(tmp_path)
    before={p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*.json')}
    assert run(manifest) == report()
    assert before == {p.relative_to(tmp_path):p.read_bytes() for p in tmp_path.rglob('*.json')}


@pytest.mark.parametrize('mode',['duplicate_market','unknown_market','extra_field','duplicate_json','escape','absolute','too_many','wrong_mode'])
def test_invalid_manifest_refused(tmp_path,mode):
    manifest=exported(tmp_path); data=json.loads(manifest.read_text(encoding='utf-8'))
    if mode == 'duplicate_market':data['markets'][1]['market']='SBER'
    if mode == 'unknown_market':data['markets'][1]['market']='OTHER'
    if mode == 'extra_field':data['markets'][0]['report']='prebuilt.json'
    if mode == 'escape':data['markets'][0]['snapshot']='../outside.json'
    if mode == 'absolute':data['markets'][0]['snapshot']=str((tmp_path/'SBER/snapshot.json').resolve())
    if mode == 'too_many':data['markets'].append(data['markets'][0])
    if mode == 'wrong_mode':data['evidence_mode']='LIVE'
    text=json.dumps(data)
    if mode == 'duplicate_json':text=text.replace('{','{"version":"other",',1)
    manifest.write_text(text,encoding='utf-8')
    with pytest.raises(ValueError):run(manifest)


@pytest.mark.parametrize('format',['json','text'])
def test_actual_six_market_command(tmp_path,format):
    manifest=exported(tmp_path)
    result=subprocess.run([sys.executable,'scripts/g2_six_market_report.py','--manifest',str(manifest),'--format',format],capture_output=True,encoding='utf-8',check=True)
    if format == 'json':assert json.loads(result.stdout) == report()
    else:assert '# Сводка шести рынков' in result.stdout and 'Si-EXACT' in result.stdout
