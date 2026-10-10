from dataclasses import replace
import hashlib
import json
import pytest
from test_production_feature_contracts import snapshot, instrument
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.level_reaction import build_level_reaction_report, canonical_bytes
from birzha.domain.market import Candle, CandleSeries
from birzha.storage.level_reaction import LevelReactionJournal, ReactionCollisionError
from scripts.g2_level_reaction import run

CUTOFF = '2026-03-02T14:00:00+03:00'


def forecast():
    snap=snapshot(60)
    snap=replace(snap,d1=replace(snap.d1,support_20=100.,resistance_20=110.))
    return build_forecast_from_snapshot(snap).to_dict()


def series(closes=(101.,99.,100.,101.)):
    candles=[]
    for i,close in enumerate(closes):
        begin=f'2026-03-02T{9+i:02}:00:00+03:00'
        end=f'2026-03-02T{9+i:02}:59:59+03:00'
        receipt=f'2026-03-02T{10+i:02}:00:00+03:00'
        candles.append(Candle(100.,close,max(102.,close),min(98.,close),None,None,begin,end,True,receipt,'EXACT',receipt,'r1','TEST_RECEIPT'))
    return CandleSeries(instrument(),'H1',tuple(candles),'TEST_RECEIPT')


def observe(s=None,f=None,cutoff=CUTOFF):
    return build_level_reaction_report(f or forecast(),s or series(),observation_at=cutoff)


def test_exact_contacts_side_changes_and_return_preserve_original_forecast():
    frozen=forecast()
    before=canonical_bytes(frozen)
    report=observe(f=frozen)
    level=report['levels'][0]
    assert level['range_contact_bars']==4
    assert level['close_counts']=={'ABOVE':2,'ON':1,'BELOW':1}
    assert level['close_side_changes']==1
    assert level['returns_to_initial_close_side']==1
    assert level['bars'][0]['previous_close_side'] is None
    assert level['bars'][-1]['returned_to_initial_close_side']
    for name in ('acceptance','rejection','failed_breakout','holding_power'):
        assert level[name]=='UNAVAILABLE'
    assert level['level_strength']=='UNKNOWN' and level['control']=='UNKNOWN'
    assert canonical_bytes(frozen)==before
    assert report['forecast_sha256']==hashlib.sha256(before).hexdigest()
    assert report['coverage']=='SUPPLIED_BARS_ONLY_CALENDAR_CONTINUITY_UNPROVEN'


@pytest.mark.parametrize('changes',[
    {'completed':False},{'completed':1},{'close':None},{'close':True},{'high':float('nan')},
    {'high':99.},{'begin':'bad'},{'begin':'2026-03-01T17:00:00+03:00'},
    {'end':'2026-03-03T10:00:00+03:00'},{'observed_at':None},
    {'observed_at':'2026-03-02T10:00:00'},
    {'observed_at':'2026-03-03T10:00:00+03:00'},
    {'available_at':None},{'available_at_confidence':'INFERRED'},
    {'available_at_confidence':'UNKNOWN'},
    {'available_at':'2026-03-02T09:00:00+03:00'},
    {'available_at':'2026-03-02T11:00:00+03:00'},
])
def test_unproven_or_future_bar_closes_entire_observation(changes):
    s=series()
    bad=replace(s,candles=(replace(s.candles[0],**changes),*s.candles[1:]))
    with pytest.raises(ValueError): observe(s=bad)


@pytest.mark.parametrize('mode',['duplicate','reverse','overlap','wrong_contract','wrong_symbol','wrong_tf'])
def test_series_conflicts_refused(mode):
    s=series()
    if mode=='duplicate': s=replace(s,candles=(s.candles[0],*s.candles))
    if mode=='reverse': s=replace(s,candles=tuple(reversed(s.candles)))
    if mode=='overlap': s=replace(s,candles=(s.candles[0],replace(s.candles[1],begin=s.candles[0].end),*s.candles[2:]))
    if mode=='wrong_contract': s=replace(s,instrument=replace(s.instrument,secid='OTHER'))
    if mode=='wrong_symbol': s=replace(s,instrument=replace(s.instrument,symbol='OTHER'))
    if mode=='wrong_tf': s=replace(s,timeframe='M1')
    with pytest.raises(ValueError): observe(s=s)


def test_missing_observations_are_null_not_zero():
    report=observe(s=replace(series(),candles=()))
    assert report['supplied_bar_count']==0
    assert all(level['status']=='UNAVAILABLE' and level['range_contact_bars'] is None and level['bars']==[] for level in report['levels'])


def test_known_zero_contacts_are_zero_and_approximate_source_retained():
    s=series((101.,102.))
    s=replace(s,candles=tuple(replace(c,open=101.,low=101.) for c in s.candles))
    report=observe(s=s)
    assert report['levels'][0]['range_contact_bars']==0
    from test_price_level_evidence import with_profile
    frozen=build_forecast_from_snapshot(with_profile()).to_dict()
    assert all(level['status']=='AVAILABLE_APPROXIMATE' for level in observe(f=frozen)['levels'][-3:])


@pytest.mark.parametrize('price',[0.,-100.])
def test_zero_and_negative_levels_are_not_missing(price):
    snap=snapshot(60)
    snap=replace(snap,d1=replace(snap.d1,support_20=price,resistance_20=110.))
    report=observe(f=build_forecast_from_snapshot(snap).to_dict())
    assert report['levels'][0]['price']==price
    assert report['levels'][0]['status']=='AVAILABLE'


def test_on_level_is_not_assigned_to_either_side_and_intra_bar_order_unknown():
    report=observe(s=series((100.,100.)))
    level=report['levels'][0]
    assert level['close_counts']=={'ABOVE':0,'ON':2,'BELOW':0}
    assert level['close_side_changes']==0 and level['returns_to_initial_close_side']==0
    assert all(b['high_above_level'] and b['low_below_level'] for b in level['bars'])
    assert level['failed_breakout']=='UNAVAILABLE'


@pytest.mark.parametrize('cutoff',['2026-03-01T18:00:00+03:00','2026-03-02T14:00:00','bad'])
def test_explicit_later_observation_required(cutoff):
    with pytest.raises(ValueError): observe(cutoff=cutoff)


def test_frozen_identity_and_schema_required():
    for field,value in (('forecast_id',None),('record_version','FORECAST_RECORD_V1_PROTOCOL_08')):
        frozen=forecast();frozen[field]=value
        with pytest.raises(ValueError): observe(f=frozen)
    frozen=forecast();frozen['level_evidence'][0]['source_end']='2026-03-03T18:00:00'
    with pytest.raises(ValueError): observe(f=frozen)


def test_deterministic_journal_idempotence_later_append_and_tamper_detection(tmp_path):
    journal=LevelReactionJournal(tmp_path/'observations.sqlite3')
    try:
        frozen=forecast()
        first=journal.observe(frozen,series(),observation_at=CUTOFF)
        assert journal.observe(frozen,series(),observation_at=CUTOFF)==first
        assert journal.get(first['report_id'])==first
        second=journal.observe(frozen,series(),observation_at='2026-03-02T15:00:00+03:00')
        assert first['report_id']!=second['report_id']
        assert journal.connection.execute('SELECT count(*) FROM reaction_reports').fetchone()[0]==2
        journal.connection.execute('UPDATE reaction_reports SET payload=? WHERE report_id=?',(b'{}',first['report_id']))
        journal.connection.commit()
        with pytest.raises(ReactionCollisionError): journal.get(first['report_id'])
        with pytest.raises(ReactionCollisionError): journal.observe(frozen,series(),observation_at=CUTOFF)
        assert journal.get(second['report_id'])==second
    finally: journal.close()


def test_cli_inputs_unchanged_and_duplicate_json_refused(tmp_path):
    fp,sp=tmp_path/'forecast.json',tmp_path/'candles.json'
    fp.write_bytes(canonical_bytes(forecast()));sp.write_bytes(canonical_bytes(series().to_dict()))
    before=(fp.read_bytes(),sp.read_bytes())
    report=run(fp,sp,observation_at=CUTOFF,journal_path=tmp_path/'reaction.sqlite3')
    assert report['supplied_bar_count']==4
    assert (fp.read_bytes(),sp.read_bytes())==before
    with pytest.raises(ValueError): run(fp,sp,observation_at=CUTOFF,journal_path=fp)
    sp.write_text('{"candles":[],"candles":[]}',encoding='utf-8')
    with pytest.raises(ValueError,match='duplicate JSON'): run(fp,sp,observation_at=CUTOFF)



def test_same_forecast_identity_cannot_acquire_a_second_payload(tmp_path):
    journal=LevelReactionJournal(tmp_path/'anchors.sqlite3')
    try:
        original=forecast()
        first=journal.observe(original,series(),observation_at=CUTOFF)
        changed=forecast();changed['reasons'].append('edited after reaction')
        with pytest.raises(ReactionCollisionError): journal.observe(changed,series(),observation_at=CUTOFF)
        assert journal.get(first['report_id'])==first
        assert journal.connection.execute('SELECT count(*) FROM reaction_reports').fetchone()[0]==1
    finally: journal.close()


def test_equivalent_timezone_receipts_and_gaps_do_not_claim_continuity():
    s=series()
    shifted=replace(s,candles=tuple(replace(c,observed_at=f'2026-03-02T{7+i:02}:00:00Z') for i,c in enumerate(s.candles)))
    assert observe(s=shifted)['levels'][0]['range_contact_bars']==4
    gapped=replace(s,candles=(s.candles[0],s.candles[-1]))
    report=observe(s=gapped)
    assert report['supplied_bar_count']==2
    assert 'CONTINUITY_UNPROVEN' in report['coverage']
    assert report['levels'][0]['holding_power']=='UNAVAILABLE'


def test_invalid_observation_writes_no_report_or_forecast_anchor(tmp_path):
    journal=LevelReactionJournal(tmp_path/'refused.sqlite3')
    try:
        s=series();s=replace(s,candles=(replace(s.candles[0],observed_at=None),))
        with pytest.raises(ValueError): journal.observe(forecast(),s,observation_at=CUTOFF)
        assert journal.connection.execute('SELECT count(*) FROM reaction_reports').fetchone()[0]==0
        assert journal.connection.execute('SELECT count(*) FROM reaction_forecast_anchors').fetchone()[0]==0
    finally: journal.close()



def test_readable_report_explains_missing_values_and_limits():
    from scripts.g2_level_reaction import render_report
    rendered=render_report(observe())
    assert 'нет данных' in rendered
    assert 'нижняя граница за 20 свечей' in rendered
    assert 'полнота торгового календаря не подтверждена' in rendered
    assert 'Исходный прогноз сохранён без изменений' in rendered
