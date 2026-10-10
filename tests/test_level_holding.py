from dataclasses import replace
import hashlib
import json
import pytest
from test_level_reaction import forecast, series, CUTOFF
from birzha.application.level_reaction import canonical_bytes
from birzha.application.level_holding import build_level_holding_report
from birzha.storage.level_holding import LevelHoldingJournal
from birzha.storage.level_reaction import ReactionCollisionError
from scripts.g2_level_holding import run, render_report


def schedule(s=None):
    s=s or series()
    return {'version':'EXPECTED_BAR_SCHEDULE_V1','origin':'EXTERNAL_EXPECTED_SCHEDULE','complete':True,'symbol':s.instrument.symbol,'secid':s.instrument.secid,'timeframe':s.timeframe,'source':'SYNTHETIC_TEST_SCHEDULE','observed_at':'2026-03-02T08:00:00+03:00','window_start':'2026-03-02T09:00:00+03:00','window_end':CUTOFF,'expected_bars':[{'begin':c.begin,'end':c.end} for c in s.candles]}


def observe(s=None,calendar=None):
    s=s or series()
    return build_level_holding_report(forecast(),s,observation_at=CUTOFF,schedule_bytes=canonical_bytes(calendar or schedule(s)))


def test_complete_window_close_runs_trailing_run_and_time_are_facts():
    s=series((101.,101.,100.,99.))
    report=observe(s)
    level=report['levels'][0]
    assert report['continuity']['status']=='MATCHES_DECLARED_SCHEDULE'
    assert level['longest_close_runs']=={'ABOVE':2,'ON':1,'BELOW':1}
    assert level['trailing_close_run']['side']=='BELOW'
    assert level['close_runs'][0]['close_to_close_elapsed_seconds']==3600.
    assert level['close_runs'][1]['close_to_close_elapsed_seconds']==0.
    assert level['continuous_price_dwell_seconds'] is None
    assert level['holding_strength']=='UNKNOWN' and level['control']=='UNKNOWN'
    assert report['continuity']['schedule_sha256']==hashlib.sha256(canonical_bytes(schedule(s))).hexdigest()


def test_missing_expected_middle_or_tail_never_compresses_a_run():
    s=series((101.,101.,101.,101.)); expected=schedule(s)
    for candles in ((s.candles[0],s.candles[2],s.candles[3]),s.candles[:-1],()):
        report=observe(replace(s,candles=candles),expected)
        assert report['continuity']['reason']=='MISSING_EXPECTED_BARS'
        assert report['continuity']['missing']
        assert all(item['longest_close_runs'] is None for item in report['levels'])


def test_no_schedule_does_not_imply_continuity():
    report=build_level_holding_report(forecast(),series(),observation_at=CUTOFF)
    assert report['continuity']['expected_count'] is None
    assert report['continuity']['reason']=='EXPECTED_SCHEDULE_MISSING'
    assert all(item['close_runs'] is None for item in report['levels'])


def test_unexpected_bar_does_not_get_dropped_silently():
    s=series(); expected=schedule(s);expected['expected_bars']=expected['expected_bars'][:-1]
    report=observe(s,expected)
    assert report['continuity']['reason']=='UNEXPECTED_BARS'
    assert len(report['continuity']['unexpected'])==1
    assert report['levels'][0]['close_runs'] is None


@pytest.mark.parametrize('field,value',[
    ('version','OLD'),('origin','CANDLE_ROWS'),('complete',False),('complete',1),
    ('symbol','OTHER'),('secid','OTHER'),('timeframe','M1'),('source',''),
    ('observed_at',None),('observed_at','2026-03-02T08:00:00'),
    ('observed_at','2026-03-03T08:00:00+03:00'),
    ('window_start','2026-03-01T17:00:00+03:00'),
    ('window_start','2026-03-02T10:00:00+03:00'),
    ('window_end','2026-03-03T14:00:00+03:00'),('expected_bars',None),
])
def test_unknown_or_conflicting_calendar_refused(field,value):
    expected=schedule();expected[field]=value
    with pytest.raises(ValueError):observe(calendar=expected)


@pytest.mark.parametrize('mode',['duplicate','reverse','overlap','outside','zero_length'])
def test_invalid_expected_slots_refused(mode):
    expected=schedule(); slots=expected['expected_bars']
    if mode=='duplicate': slots.append(slots[-1])
    if mode=='reverse': slots.reverse()
    if mode=='overlap':slots[1]['begin']=slots[0]['end']
    if mode=='outside':slots[0]['begin']='2026-03-01T08:00:00+03:00'
    if mode=='zero_length':slots[0]['end']=slots[0]['begin']
    with pytest.raises(ValueError):observe(calendar=expected)


def test_schedule_compares_instants_not_timezone_spelling():
    expected=schedule()
    for i,slot in enumerate(expected['expected_bars']):
        slot['begin']=f'2026-03-02T{6+i:02}:00:00Z'
        slot['end']=f'2026-03-02T{6+i:02}:59:59Z'
    assert observe(calendar=expected)['continuity']['status']=='MATCHES_DECLARED_SCHEDULE'


def test_external_calendar_can_have_session_breaks_but_not_claim_dwell():
    s=series();s=replace(s,candles=(s.candles[0],s.candles[-1]))
    report=observe(s)
    assert report['continuity']['status']=='MATCHES_DECLARED_SCHEDULE'
    assert report['levels'][0]['longest_close_runs']['ABOVE']==2
    assert report['levels'][0]['close_runs'][0]['close_to_close_elapsed_seconds']==10800.
    assert report['levels'][0]['continuous_price_dwell_seconds'] is None


def test_empty_declared_window_is_missing_observation_not_zero_holding():
    s=replace(series(),candles=())
    report=observe(s)
    assert report['continuity']['expected_count']==0
    assert all(level['status']=='UNAVAILABLE' and level['longest_close_runs'] is None for level in report['levels'])


def test_coincident_and_approximate_origins_are_preserved():
    from test_price_level_evidence import with_profile
    f=__import__('birzha.application.forecast',fromlist=['build_forecast_from_snapshot']).build_forecast_from_snapshot(with_profile()).to_dict()
    report=build_level_holding_report(f,series(),observation_at=CUTOFF,schedule_bytes=canonical_bytes(schedule()))
    assert len(report['levels'])==9
    assert report['levels'][2]['price']==report['levels'][4]['price']
    assert all(item['status']=='AVAILABLE_APPROXIMATE' for item in report['levels'][-3:])


def test_journal_retries_new_observation_and_forecast_collision(tmp_path):
    journal=LevelHoldingJournal(tmp_path/'hold.sqlite3')
    try:
        f=forecast();s=series();raw=canonical_bytes(schedule())
        report=journal.observe(f,s,observation_at=CUTOFF,schedule_bytes=raw)
        assert journal.observe(f,s,observation_at=CUTOFF,schedule_bytes=raw)==report
        assert journal.get(report['report_id'])==report
        later=journal.observe(f,s,observation_at='2026-03-02T15:00:00+03:00',schedule_bytes=raw)
        assert later['report_id']!=report['report_id']
        f['reasons'].append('changed')
        with pytest.raises(ReactionCollisionError):journal.observe(f,s,observation_at=CUTOFF,schedule_bytes=raw)
        journal.connection.execute('UPDATE holding_reports SET payload=? WHERE report_id=?',(b'{}',report['report_id']))
        journal.connection.commit()
        with pytest.raises(ReactionCollisionError):journal.get(report['report_id'])
    finally:journal.close()


def test_cli_sources_unchanged_duplicate_json_and_source_overwrite_closed(tmp_path):
    f,s,c=[tmp_path/x for x in ('forecast.json','candles.json','schedule.json')]
    for path,data in ((f,forecast()),(s,series().to_dict()),(c,schedule())):path.write_bytes(canonical_bytes(data))
    before=[p.read_bytes() for p in (f,s,c)]
    report=run(f,s,observation_at=CUTOFF,schedule_path=c,journal_path=tmp_path/'journal.sqlite3')
    assert before==[p.read_bytes() for p in (f,s,c)]
    assert 'Максимум закрытий выше' in render_report(report)
    assert 'непрерывное пребывание' in render_report(report)
    for path in (f,s,c):
        with pytest.raises(ValueError):run(f,s,observation_at=CUTOFF,schedule_path=c,journal_path=path)
    c.write_text('{"version":1,"version":2}',encoding='utf-8')
    with pytest.raises(ValueError):run(f,s,observation_at=CUTOFF,schedule_path=c)



def test_d1_matches_session_date_not_variable_last_trade_end():
    s=series();s=replace(s,timeframe='D1',candles=(s.candles[0],))
    expected=schedule(s)
    expected['expected_bars'][0]['end']='2026-03-02T13:59:59+03:00'
    assert observe(s,expected)['continuity']['matching_identity']=='MOEX_SESSION_DATE'
    assert observe(s,expected)['levels'][0]['longest_close_runs']['ABOVE']==1


def test_d1_duplicate_session_is_not_two_consecutive_days():
    s=replace(series(),timeframe='D1')
    with pytest.raises(ValueError,match='duplicate trading session'):observe(s)


def test_intraday_changed_end_is_missing_and_unexpected_not_same_bar():
    expected=schedule()
    expected['expected_bars'][0]['end']='2026-03-02T09:59:58+03:00'
    report=observe(calendar=expected)
    assert report['continuity']['missing'] and report['continuity']['unexpected']
    assert report['levels'][0]['longest_close_runs'] is None
