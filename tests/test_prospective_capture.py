"""Entirely isolated offline tests for the prospective receipt / outcome pilot."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone, timedelta
import sqlite3
from pathlib import Path
import pytest
from birzha.application.prospective_capture import (
    ProspectivePilotLedger,CaptureEvidence,CompletedSessions,AdmissionRefused,ImmutableCollision,
)

BASE = datetime(2026,10,10,12,0,tzinfo=timezone.utc)


@dataclass(frozen=True)
class FakeForecastRecord:
    fid: str = 'fcst_pilot_001'
    secid: str = 'SBER'
    price: float = 100.0
    t0: str = '2026-10-10T11:59:30Z'
    def to_dict(self):
        return {'forecast_id':self.fid,'symbol':'SBER','secid':self.secid,
            'created_at_t0':self.t0,'record_version':'FORECAST_RECORD_V1_PROTOCOL_08',
            'snapshot_id':'snap_verified_fixture_01','snapshot_contract_version':'MARKET_SNAPSHOT_V2',
            'engine_version':'PILOT_UNCALIBRATED','reference_price':self.price,
            'horizons':[{'sessions':h,'direction':'UP','signal_strength':0.1,'expected_move_pct':None,'adverse_move_pct':None} for h in (5,10,20)]}


def source(**overrides):
    args={'source_payload':b'{"sber":"completed_bars_up_to_10oct"}',
        'source_observed_at':'2026-10-10T11:59:25Z',
        'latest_completed_event_end':'2026-10-09T20:00:00Z'}
    args.update(overrides)
    return CaptureEvidence(**args)


def future(h=5,**overrides):
    days=tuple((BASE.date()+timedelta(days=k)).isoformat() for k in range(1,h+1))
    ends=tuple(f'{x}T20:00:00Z' for x in days)
    args={'source_payload':b'{"exchange":"new completion"}',
        'source_observed_at':f'{days[-1]}T20:01:00Z',
        'market':'SBER','secid':'SBER','session_dates':days,
        'expected_calendar_dates':days,'candle_completed_at':ends,
        'candle_close':tuple(100.0+i for i in range(1,h+1))}
    args.update(overrides)
    return CompletedSessions(**args)


def ledger(tmp_path,now=BASE):
    return ProspectivePilotLedger(tmp_path/'pilot.sqlite3',clock=lambda:now)


def test_forecast_capture_is_idempotent_and_persistent(tmp_path):
    l=ledger(tmp_path)
    rec=l.capture(FakeForecastRecord(),source());assert rec['status']=='APPENDED'
    assert rec['receipt']['pilot_only'] and rec['receipt']['strict_historical_pit'] is False
    l.close();l=ledger(tmp_path,BASE+timedelta(minutes=20))
    again=l.capture(FakeForecastRecord(),source());assert again['status']=='DUPLICATE_IDENTICAL'
    assert again['receipt']==rec['receipt']
    assert l.audit()['captures']==1
    l.close()


def test_conflicting_forecast_rejected(tmp_path):
    l=ledger(tmp_path);l.capture(FakeForecastRecord(),source())
    with pytest.raises(ImmutableCollision):l.capture(FakeForecastRecord(price=101),source())
    with pytest.raises(ImmutableCollision):l.capture(FakeForecastRecord(),source(source_payload=b'changed'))
    assert l.audit()['captures']==1;l.close()


def test_reconstructed_source_blocked(tmp_path):
    l=ledger(tmp_path)
    with pytest.raises(AdmissionRefused,match='reconstructed'):
        l.capture(FakeForecastRecord(),source(source_origin='RECONSTRUCTED_MOEX'))
    l.close()


def test_future_unobserved_bar_rejected(tmp_path):
    l=ledger(tmp_path)
    with pytest.raises(AdmissionRefused,match='future data'):
        l.capture(FakeForecastRecord(),source(latest_completed_event_end='2026-10-10T12:05:00Z'))
    assert l.audit()['captures']==0;l.close()


def test_unproven_stale_or_future_t0_rejected(tmp_path):
    l=ledger(tmp_path)
    with pytest.raises(AdmissionRefused,match='stale'):
        l.capture(FakeForecastRecord(t0='2026-10-10T11:40:00Z'),source(source_observed_at='2026-10-10T11:39:00Z'))
    with pytest.raises(AdmissionRefused,match='cannot predate'):
        l.capture(FakeForecastRecord(t0='2026-10-10T12:01:00Z'),source(source_observed_at='2026-10-10T12:00:30Z'))
    l.close()


def test_missing_snapshot_reference_or_invalid_price_fails(tmp_path):
    l=ledger(tmp_path)
    with pytest.raises(AdmissionRefused,match='reference'):
        l.capture(FakeForecastRecord(price=0),source())
    class MissingSnapshot(FakeForecastRecord):
        def to_dict(self):
            x=super().to_dict();x['snapshot_id']=None;return x
    with pytest.raises(AdmissionRefused,match='snapshot'):
        l.capture(MissingSnapshot(),source())
    l.close()


def test_invalid_or_legacy_record_contract_rejected(tmp_path):
    l=ledger(tmp_path)
    class Legacy(FakeForecastRecord):
        def to_dict(self):
            x=super().to_dict();x['record_version']='FORECAST_RECORD_LEGACY_V0';return x
    with pytest.raises(AdmissionRefused,match='contract'):
        l.capture(Legacy(),source())
    l.close()


def test_missing_forecast_or_wrong_horizon_refused(tmp_path):
    l=ledger(tmp_path,BASE+timedelta(days=30))
    with pytest.raises(AdmissionRefused,match='previously captured'):
        l.observe('missing',5,future())
    with pytest.raises(AdmissionRefused,match='horizon'):
        l.observe('missing',3,future(3))
    l.close()


def test_future_maturity_appends_without_mutating_forecast(tmp_path):
    l=ledger(tmp_path);l.capture(FakeForecastRecord(),source())
    original=l.audit()
    l.close();now=BASE+timedelta(days=30);l=ledger(tmp_path,now)
    a=l.observe('fcst_pilot_001',5,future())
    assert a['status']=='APPENDED' and a['outcome']['direction_hit'] is True
    assert a['outcome']['target_close']==105.0
    assert l.audit()['captures']==original['captures'] and l.audit()['outcomes']==1
    l.close()


def test_duplicate_outcome_idempotent_revision_collision(tmp_path):
    l=ledger(tmp_path);l.capture(FakeForecastRecord(),source());l.close()
    l=ledger(tmp_path,BASE+timedelta(days=30));f=future()
    assert l.observe('fcst_pilot_001',5,f)['status']=='APPENDED'
    assert l.observe('fcst_pilot_001',5,f)['status']=='DUPLICATE_IDENTICAL'
    with pytest.raises(ImmutableCollision):l.observe('fcst_pilot_001',5,future(candle_close=(111,112,113,114,115)))
    assert l.audit()['outcomes']==1;l.close()


def test_cross_secid_or_gapped_calendar_rejected(tmp_path):
    l=ledger(tmp_path);l.capture(FakeForecastRecord(),source());l.close()
    l=ledger(tmp_path,BASE+timedelta(days=30))
    with pytest.raises(AdmissionRefused,match='SECID'):
        l.observe('fcst_pilot_001',5,future(secid='OTHER'))
    dates=list(future().session_dates);dates[2]='2026-10-20'
    with pytest.raises(AdmissionRefused,match='missing, extra'):
        l.observe('fcst_pilot_001',5,future(session_dates=tuple(dates)))
    assert l.audit()['outcomes']==0;l.close()


def test_observation_before_maturity_rejected(tmp_path):
    l=ledger(tmp_path);l.capture(FakeForecastRecord(),source());l.close()
    l=ledger(tmp_path,BASE+timedelta(days=30))
    with pytest.raises(AdmissionRefused,match='uncompleted'):
        l.observe('fcst_pilot_001',5,future(source_observed_at='2026-10-12T15:00:00Z'))
    l.close()


def test_wrong_reference_path_refused(tmp_path):
    path=tmp_path/'existing.sqlite3'
    conn=sqlite3.connect(path);conn.execute('CREATE TABLE other(x INTEGER)');conn.close()
    with pytest.raises(AdmissionRefused,match='unrelated tables'):
        ProspectivePilotLedger(path)


def test_audit_detects_blob_tampering(tmp_path):
    l=ledger(tmp_path);l.capture(FakeForecastRecord(),source());l.close()
    conn=sqlite3.connect(tmp_path/'pilot.sqlite3')
    conn.execute('DROP TRIGGER captures_no_update')
    conn.execute('UPDATE captures SET input_blob=?',(b'tampered',));conn.commit();conn.close()
    l=ledger(tmp_path)
    with pytest.raises(ImmutableCollision,match='corrupt capture'):
        l.audit()
    l.close()


def test_never_accept_timezone_naive_t0(tmp_path):
    l=ledger(tmp_path)
    with pytest.raises(AdmissionRefused,match='timezone'):
        l.capture(FakeForecastRecord(t0='2026-10-10T11:59:30'),source())
    l.close()


def test_database_triggers_refuse_update_and_delete(tmp_path):
    l=ledger(tmp_path);l.capture(FakeForecastRecord(),source());l.close()
    with sqlite3.connect(tmp_path/'pilot.sqlite3') as conn:
        with pytest.raises(sqlite3.IntegrityError,match='append-only capture'):
            conn.execute('UPDATE captures SET input_sha=\'edited\'')
        with pytest.raises(sqlite3.IntegrityError,match='append-only capture'):
            conn.execute('DELETE FROM captures')


def test_moscow_local_midnight_completion_not_confused_with_utc_date(tmp_path):
    l=ledger(tmp_path);l.capture(FakeForecastRecord(),source());l.close()
    l=ledger(tmp_path,BASE+timedelta(days=30))
    f=future(candle_completed_at=tuple(f'{day}T00:30:00+03:00' for day in future().session_dates))
    # Every completed candle is an offset-aware local exchange timestamp, even
    # when its UTC date is the previous day. The final source receipt is later.
    assert l.observe('fcst_pilot_001',5,f)['status']=='APPENDED'
    l.close()



def test_pilot_forecast_at_utc_midnight_refuses_same_moscow_day_as_future(tmp_path):
    now=datetime(2026,10,10,23,33,tzinfo=timezone.utc)
    p=ProspectivePilotLedger(tmp_path/"pilot.sqlite3",clock=lambda:now)
    assert p.capture(
        FakeForecastRecord(t0="2026-10-10T23:30:00Z"),
        source(source_observed_at="2026-10-10T23:29:00Z"),
    )["status"]=="APPENDED"
    p.close()
    p=ledger(tmp_path,now+timedelta(days=30))
    with pytest.raises(AdmissionRefused,match="not after forecast local date"):
        p.observe("fcst_pilot_001",5,future())
    assert p.audit()["outcomes"]==0
    p.close()


def test_pilot_accepts_later_moscow_session_window_and_audits_full_window(tmp_path):
    now=datetime(2026,10,10,23,33,tzinfo=timezone.utc)
    p=ProspectivePilotLedger(tmp_path/"pilot.sqlite3",clock=lambda:now)
    assert p.capture(
        FakeForecastRecord(t0="2026-10-10T23:30:00Z"),
        source(source_observed_at="2026-10-10T23:29:00Z"),
    )["status"]=="APPENDED"
    p.close()
    dates=tuple(f"2026-10-{n:02d}" for n in (12,13,14,15,16))
    fixed=future(
        session_dates=dates,expected_calendar_dates=dates,
        candle_completed_at=tuple(f"{day}T20:00:00Z" for day in dates),
        source_observed_at="2026-10-16T20:01:00Z")
    p=ledger(tmp_path,now+timedelta(days=30))
    added=p.observe("fcst_pilot_001",5,fixed)
    assert added["status"]=="APPENDED"
    assert added["outcome"]["original_future_window"]["session_dates"]==list(dates)
    assert len(added["outcome"]["original_future_window_sha256"])==64
    assert p.audit()["outcomes"]==1
    with pytest.raises(ImmutableCollision,match="changed source"):
        revised=future(session_dates=dates,expected_calendar_dates=dates,
            candle_completed_at=tuple(f"{day}T20:00:00Z" for day in dates),
            source_observed_at="2026-10-16T20:01:00Z",
            candle_close=(999,102,103,104,105))
        p.observe("fcst_pilot_001",5,revised)
    p.close()
    p=ledger(tmp_path,now+timedelta(days=30))
    assert p.audit()["outcomes"]==1
    p.close()


def test_pilot_cold_audit_rejects_rehashed_illegal_first_moscow_session(tmp_path):
    from hashlib import sha256
    import json
    now=datetime(2026,10,10,23,33,tzinfo=timezone.utc)
    p=ProspectivePilotLedger(tmp_path/"pilot.sqlite3",clock=lambda:now)
    p.capture(FakeForecastRecord(t0="2026-10-10T23:30:00Z"),
              source(source_observed_at="2026-10-10T23:29:00Z"))
    p.close()
    dates=tuple(f"2026-10-{n:02d}" for n in (12,13,14,15,16))
    p=ledger(tmp_path,now+timedelta(days=30))
    p.observe("fcst_pilot_001",5,future(
        session_dates=dates,expected_calendar_dates=dates,
        candle_completed_at=tuple(f"{day}T20:00:00Z" for day in dates),
        source_observed_at="2026-10-16T20:01:00Z"))
    p.close()
    database=tmp_path/"pilot.sqlite3"
    db=sqlite3.connect(database)
    db.execute("DROP TRIGGER outcomes_no_update")
    original=db.execute("SELECT payload_json FROM outcomes").fetchone()[0]
    result=json.loads(original)
    window=result["original_future_window"]
    window["session_dates"][0]="2026-10-11"
    window["expected_calendar_dates"][0]="2026-10-11"
    canon=lambda v:json.dumps(v,sort_keys=True,ensure_ascii=False,
                              separators=(",",":"),allow_nan=False)
    digest=lambda t:sha256(t.encode("utf8")).hexdigest()
    result["original_future_window_sha256"]=digest(canon(window))
    encoded=canon(result)
    db.execute("UPDATE outcomes SET payload_json=?,payload_sha=?",
               (encoded,digest(encoded)))
    db.commit();db.close()
    p=ledger(tmp_path,now+timedelta(days=30))
    with pytest.raises(ImmutableCollision,match="future sessions detached"):
        p.audit()
    p.close()
