from dataclasses import replace
import hashlib
import json
import subprocess
import sys
import pytest
from test_linked_level_report import inputs, exported
from test_level_reaction import series, CUTOFF
from test_level_holding import schedule
from birzha.application.level_reaction import canonical_bytes
from birzha.application.combined_market_report import build_combined_market_report, render_combined_market_report
from scripts.g2_combined_market_report import run


def combined(s=None, calendar=True):
    snap, forecast = inputs()
    s = series() if s is None else s
    return build_combined_market_report(snap, forecast, series=s, observation_at=CUTOFF,
        schedule_bytes=canonical_bytes(schedule(s)) if calendar else None)


def test_source_binding_and_complete_content_hash():
    report = combined()
    movement, levels = report['movement_at_t0'], report['level_report']
    for key in ('snapshot_id','snapshot_sha256','snapshot_contract_version','symbol','secid'):
        assert report[key] == movement[key] == levels[key]
    assert report['t0'] == movement['t0'] == levels['created_at_t0']
    assert report['forecast_sha256'] == levels['forecast_sha256']
    body = {k:v for k,v in report.items() if k != 'report_id'}
    assert report['report_id'] == 'combined_market_' + hashlib.sha256(canonical_bytes(body)).hexdigest()
    assert report == combined()
    assert report['alignment_score'] is None and report['control'] == 'UNKNOWN'
    assert report['predictive_quality'] == 'NOT_MEASURED'


def test_later_prices_cannot_change_movement_or_location():
    first = combined(series((101.,99.,100.,101.)))
    second = combined(series((120.,121.,122.,123.)))
    assert first['movement_at_t0'] == second['movement_at_t0']
    assert first['level_report']['at_t0'] == second['level_report']['at_t0']
    assert first['forecast_sha256'] == second['forecast_sha256']
    assert first['level_report']['after_t0'] != second['level_report']['after_t0']
    assert first['report_id'] != second['report_id']
    assert first['later_data_used_for_movement'] is False


def test_t0_only_and_missing_calendar_do_not_invent_future_or_runs():
    snap, forecast = inputs()
    report = build_combined_market_report(snap, forecast)
    assert report['observation_at'] is None and report['level_report']['after_t0'] is None
    assert 'Последующие свечи не переданы' in render_combined_market_report(report)
    without = combined(calendar=False)
    first = without['level_report']['rows'][0]['after_t0']
    assert first['range_contact_bars'] == 4 and first['longest_close_runs'] is None
    assert without['movement_at_t0'] == combined()['movement_at_t0']


@pytest.mark.parametrize('mode',['forecast','snapshot','no_cutoff','no_series','unfinished','late_receipt','instrument','gap'])
def test_invalid_or_incomplete_inputs_fail_closed(mode):
    snap, forecast = inputs(); s = series()
    args = {'series':s,'observation_at':CUTOFF,'schedule_bytes':canonical_bytes(schedule(s))}
    if mode == 'forecast': forecast['snapshot_id'] = 'other'
    if mode == 'snapshot': snap = replace(snap, source='OTHER')
    if mode == 'no_cutoff': args.pop('observation_at')
    if mode == 'no_series': args.pop('series')
    if mode == 'unfinished': args['series'] = replace(s,candles=(replace(s.candles[0],completed=False),*s.candles[1:]))
    if mode == 'late_receipt': args['series'] = replace(s,candles=(replace(s.candles[0],observed_at='2026-03-03T10:00:00+03:00'),*s.candles[1:]))
    if mode == 'instrument': args['series'] = replace(s,instrument=replace(s.instrument,secid='OTHER'))
    if mode == 'gap':
        args['series'] = replace(s,candles=(s.candles[0],*s.candles[2:]))
        report = build_combined_market_report(snap,forecast,**args)
        assert report['level_report']['rows'][0]['after_t0']['longest_close_runs'] is None
        return
    with pytest.raises(ValueError): build_combined_market_report(snap,forecast,**args)


def test_read_only_file_runner(tmp_path):
    paths = exported(tmp_path); before = {k:p.read_bytes() for k,p in paths.items()}
    report = run(paths['snapshot'],paths['forecast'],candles_path=paths['candles'],
        observation_at=CUTOFF,schedule_path=paths['schedule'])
    assert report == combined()
    assert before == {k:p.read_bytes() for k,p in paths.items()}
    text = render_combined_market_report(report)
    assert text.startswith('# Движение цены и реакции на уровни')
    assert text.index('## Сравнение движения цены') < text.index('### После записи прогноза')
    assert 'Эти данные не были входом прежнего прогноза' in text


@pytest.mark.parametrize('mode',['count','duplicate'])
def test_ambiguous_candle_export_refused(tmp_path,mode):
    paths = exported(tmp_path)
    text = paths['candles'].read_text(encoding='utf-8')
    if mode == 'duplicate': text = text.replace('{','{"count":999,',1)
    else:
        data = json.loads(text); data['count'] = 999; text = json.dumps(data)
    paths['candles'].write_text(text,encoding='utf-8')
    with pytest.raises(ValueError):
        run(paths['snapshot'],paths['forecast'],candles_path=paths['candles'],observation_at=CUTOFF)


@pytest.mark.parametrize('format',['text','json'])
def test_actual_command(tmp_path,format):
    paths = exported(tmp_path)
    result = subprocess.run([sys.executable,'scripts/g2_combined_market_report.py',
        '--snapshot',str(paths['snapshot']),'--forecast',str(paths['forecast']),
        '--candles',str(paths['candles']),'--observation-at',CUTOFF,
        '--schedule',str(paths['schedule']),'--format',format],capture_output=True,encoding='utf-8',check=True)
    if format == 'json': assert json.loads(result.stdout) == combined()
    else: assert '### После записи прогноза' in result.stdout and 'за 20 шагов' in result.stdout


def test_later_cutoff_changes_only_observation_part():
    snap, forecast = inputs(); s = series()
    a = build_combined_market_report(snap,forecast,series=s,observation_at=CUTOFF)
    b = build_combined_market_report(snap,forecast,series=s,observation_at='2026-03-02T15:00:00+03:00')
    assert a['movement_at_t0'] == b['movement_at_t0']
    assert a['level_report']['at_t0'] == b['level_report']['at_t0']
    assert a['forecast_sha256'] == b['forecast_sha256']
    assert a['report_id'] != b['report_id']
