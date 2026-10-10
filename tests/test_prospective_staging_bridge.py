"""Negative, crash/retry and canonical-journal integration tests, offline only."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib, json
import pytest

from birzha.application.prospective_capture import (
    AdmissionRefused, CaptureEvidence, CompletedSessions, ImmutableCollision, ProspectivePilotLedger,
)
from birzha.application.prospective_staging_bridge import (
    CanonicalProspectiveStagingBridge,
)

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Forecast:
    forecast_id: str = 'bridge_test_001'
    ref: float = 100.0

    def to_dict(self):
        return {'forecast_id': self.forecast_id, 'symbol': 'SBER', 'secid': 'SBER',
                'record_version': 'FORECAST_RECORD_V1_PROTOCOL_08',
                'snapshot_id': 'snap_bridge_fixture', 'snapshot_contract_version': 'MARKET_SNAPSHOT_V2',
                'created_at_t0': '2026-10-10T11:59:30Z',
                'reference_price': self.ref, 'engine_version': 'TEST_ONLY',
                'horizons': [{'sessions': h, 'direction': 'UP', 'signal_strength': .1,
                              'expected_move_pct': None, 'adverse_move_pct': None} for h in (5, 10, 20)]}


def hash_record(rec):
    return hashlib.sha256(json.dumps(rec.to_dict(), sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Result:
    payload_hash: str
    status: str


class ForecastJournalFake:
    def __init__(self, path):
        self.path = str(path)
        self._items = {}
        self.fail_next = False

    def get(self, fid):
        return self._items.get(fid)

    def append(self, item):
        if self.fail_next:
            self.fail_next = False
            raise OSError('test simulated canonical write failure')
        fid = item.to_dict()['forecast_id']
        if fid in self._items and hash_record(self._items[fid]) != hash_record(item):
            raise ImmutableCollision('canonical collision')
        is_dup = fid in self._items
        self._items[fid] = item
        return Result(hash_record(item), 'DUPLICATE_IDENTICAL' if is_dup else 'APPENDED')


class OutcomeJournalFake:
    def __init__(self, path):
        self.path = str(path)
        self._items = {}
        self.fail_next = False

    def list_for_forecast(self, fid):
        return [v for (forecast, _), v in sorted(self._items.items()) if forecast == fid]

    def append(self, item):
        if self.fail_next:
            self.fail_next = False
            raise OSError('test simulated canonical outcome write failure')
        key = (item.forecast_id, item.horizon_sessions)
        if key in self._items and self._items[key] != item:
            raise ImmutableCollision('outcome collision')
        status = 'DUPLICATE_IDENTICAL' if key in self._items else 'APPENDED'
        self._items[key] = item
        return status


def source(**kw):
    args = dict(source_payload=b'original provider bytes fixture',
                source_observed_at='2026-10-10T11:59:25Z',
                latest_completed_event_end='2026-10-09T20:00:00Z')
    args.update(kw)
    return CaptureEvidence(**args)


def later(h=5, **kw):
    # Fixture calendar is not asserted to be a real MOEX exchange calendar.
    days = tuple((T0.date() + timedelta(days=i)).isoformat() for i in range(1, h+1))
    args = dict(source_payload=b'completed future fixture for tests',
                source_observed_at=f'{days[-1]}T20:01:00Z', market='SBER', secid='SBER',
                session_dates=days, expected_calendar_dates=days,
                candle_completed_at=tuple(f'{x}T20:00:00Z' for x in days),
                candle_close=tuple(100.0+i for i in range(1, h+1)))
    args.update(kw)
    return CompletedSessions(**args)


def stage(tmp_path, now=T0, forecast_journal=None, outcome_journal=None):
    pilot = ProspectivePilotLedger(tmp_path/'receipt.sqlite3', clock=lambda: now)
    f = forecast_journal or ForecastJournalFake(tmp_path/'forecast.duckdb')
    o = outcome_journal or OutcomeJournalFake(tmp_path/'outcome.duckdb')
    return CanonicalProspectiveStagingBridge(pilot, f, o, tmp_path), pilot, f, o


def test_capture_and_canonical_hash_same(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    result = bridge.capture(Forecast(), source())
    assert result['status'] == 'APPENDED'
    assert hash_record(f.get('bridge_test_001')) == result['receipt']['forecast_sha256']
    assert bridge.reconcile()['status'] == 'CONSISTENT'
    p.close()


def test_duplicate_capture_replay_preserves_original_time(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    a = bridge.capture(Forecast(), source())
    p.close()
    bridge, p, _, _ = stage(tmp_path, now=T0+timedelta(days=20),
                            forecast_journal=f, outcome_journal=o)
    b = bridge.capture(Forecast(), source())
    assert b['status'] == 'DUPLICATE_IDENTICAL' and b['receipt'] == a['receipt']
    p.close()


def test_canonical_write_failure_is_reported_and_replayable(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    f.fail_next = True
    with pytest.raises(OSError, match='simulated'):
        bridge.capture(Forecast(), source())
    x = bridge.reconcile()
    assert x['pilot_captures'] == 1 and x['pending_forecast_replay_ids'] == ['bridge_test_001']
    assert bridge.capture(Forecast(), source())['status'] == 'APPENDED'
    assert bridge.reconcile()['status'] == 'CONSISTENT'
    p.close()


def test_outcome_failure_recovered_from_pilot_without_future_redownload(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    bridge.capture(Forecast(), source())
    p.close()
    bridge, p, _, _ = stage(tmp_path, now=T0+timedelta(days=30),
                            forecast_journal=f, outcome_journal=o)
    o.fail_next = True
    with pytest.raises(OSError, match='simulated'):
        bridge.observe('bridge_test_001', 5, later())
    assert p.audit()['outcomes'] == 1 and not o.list_for_forecast('bridge_test_001')
    x = bridge.reconcile()
    assert x['status'] == 'CONSISTENT' and len(x['recovered_outcome_ids']) == 1
    assert len(o.list_for_forecast('bridge_test_001')) == 1
    assert bridge.reconcile()['recovered_outcome_ids'] == []
    p.close()


def test_future_outcomes_5_10_20_link_to_same_forecast(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    bridge.capture(Forecast(), source())
    p.close()
    bridge, p, _, _ = stage(tmp_path, now=T0+timedelta(days=45),
                            forecast_journal=f, outcome_journal=o)
    for h in (5,10,20):
        assert bridge.observe('bridge_test_001',h,later(h))['status'] == 'APPENDED'
    items = o.list_for_forecast('bridge_test_001')
    assert [x.horizon_sessions for x in items] == [5,10,20]
    assert [x.target_close for x in items] == [105,110,120]
    assert all(x.direction_hit is True and x.max_favorable_excursion_pct is None for x in items)
    assert bridge.reconcile()['pilot_outcomes'] == 3
    p.close()


def test_collision_in_canonical_forecast_before_pilot_write(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    f.append(Forecast(ref=110))
    with pytest.raises(ImmutableCollision,match='conflicting'):
        bridge.capture(Forecast(ref=100),source())
    assert p.audit()['captures'] == 0
    p.close()


def test_collision_after_pilot_capture_detected(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    bridge.capture(Forecast(),source())
    f._items['bridge_test_001'] = Forecast(ref=102)
    with pytest.raises(ImmutableCollision,match='differs'):
        bridge.reconcile()
    p.close()


def test_canonical_unreceipted_outcome_is_fail_closed(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    bridge.capture(Forecast(),source())
    p.close()
    bridge, p, _, _ = stage(tmp_path, now=T0+timedelta(days=30),
                            forecast_journal=f, outcome_journal=o)
    bridge.observe('bridge_test_001',5,later())
    o._items[('bridge_test_001',10)] = replace(o._items[('bridge_test_001',5)], horizon_sessions=10)
    with pytest.raises(ImmutableCollision,match='unreceipted'):
        bridge.reconcile()
    p.close()


def test_source_stale_or_reconstructed_rejected(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    with pytest.raises(AdmissionRefused):
        bridge.capture(Forecast(),source(source_origin='RECONSTRUCTED_MOEX'))
    with pytest.raises(AdmissionRefused):
        bridge.capture(Forecast(),source(source_observed_at='2026-10-10T12:01:00Z'))
    assert p.audit()['captures'] == 0
    p.close()


def test_wrong_market_exact_secid_calendar_rejected(tmp_path):
    bridge, p, f, o = stage(tmp_path)
    bridge.capture(Forecast(),source())
    p.close()
    bridge, p, _, _ = stage(tmp_path,now=T0+timedelta(days=45),
                            forecast_journal=f,outcome_journal=o)
    with pytest.raises(AdmissionRefused):
        bridge.observe('bridge_test_001',5,later(secid='OTHER'))
    bad=list(later().expected_calendar_dates);bad[0]='2026-12-01'
    with pytest.raises(AdmissionRefused):
        bridge.observe('bridge_test_001',5,later(expected_calendar_dates=tuple(bad)))
    assert p.audit()['outcomes'] == 0
    p.close()


def test_existing_source_identity_replay_collision(tmp_path):
    bridge,p,f,o=stage(tmp_path)
    bridge.capture(Forecast(),source())
    with pytest.raises(ImmutableCollision):
        bridge.capture(Forecast(),source(source_payload=b'new revision'))
    assert p.audit()['captures'] == 1
    p.close()


def test_production_like_or_outside_staging_refused(tmp_path):
    bridge,p,f,o=stage(tmp_path)
    with pytest.raises(AdmissionRefused):
        CanonicalProspectiveStagingBridge(p,f,o,tmp_path,mode='PRODUCTION')
    f.path='/tmp/sneaky_outside.duckdb'
    with pytest.raises(AdmissionRefused):
        CanonicalProspectiveStagingBridge(p,f,o,tmp_path)
    p.close()


def test_pilot_hash_audit_fails_closed_on_tampered_source(tmp_path):
    bridge,p,f,o=stage(tmp_path)
    bridge.capture(Forecast(),source());p.close()
    import sqlite3
    db=sqlite3.connect(tmp_path/'receipt.sqlite3')
    db.execute('DROP TRIGGER captures_no_update')
    db.execute('UPDATE captures SET input_blob=?',(b'evil',));db.commit();db.close()
    bridge,p,_,_=stage(tmp_path,forecast_journal=f,outcome_journal=o)
    with pytest.raises(ImmutableCollision,match='corrupt capture'):
        bridge.reconcile()
    p.close()


def test_actual_canonical_duckdb_forecast_and_outcome_journals(tmp_path):
    # Runs on CI with duckdb; local absence of duckdb skips this one test.
    pytest.importorskip('duckdb')
    from birzha.domain.forecast import ForecastRecord, HorizonForecast
    from birzha.storage.forecast_journal import DuckDBForecastJournal
    from birzha.storage.outcome_journal import DuckDBOutcomeJournal
    f=DuckDBForecastJournal(str(tmp_path/'canonical_forecast.duckdb'))
    o=DuckDBOutcomeJournal(str(tmp_path/'canonical_outcome.duckdb'))
    pilot=ProspectivePilotLedger(tmp_path/'receipt.sqlite3',clock=lambda:T0)
    bridge=CanonicalProspectiveStagingBridge(pilot,f,o,tmp_path)
    rec=ForecastRecord(
        forecast_id='real_dataclass_staging_test',symbol='SBER',secid='SBER',
        created_at_t0='2026-10-10T11:59:30Z',engine_version='SYNTHETIC_STAGING',
        direction='UP',signal_strength=.3,control='BUYERS',route='TREND',
        horizons=tuple(HorizonForecast(h,'UP',.3,None,None) for h in (5,10,20)),
        reasons=('offline regression',),warnings=('not live',),
        validation_status='UNVALIDATED_BASELINE',reference_price=100.,
        snapshot_id='snap_offline_regression',snapshot_contract_version='MARKET_SNAPSHOT_V2',
    )
    assert bridge.capture(rec,source())['status']=='APPENDED'
    pilot.close();f.close();o.close()
    f=DuckDBForecastJournal(str(tmp_path/'canonical_forecast.duckdb'))
    o=DuckDBOutcomeJournal(str(tmp_path/'canonical_outcome.duckdb'))
    pilot=ProspectivePilotLedger(tmp_path/'receipt.sqlite3',clock=lambda:T0+timedelta(days=60))
    bridge=CanonicalProspectiveStagingBridge(pilot,f,o,tmp_path)
    assert bridge.reconcile()['status']=='CONSISTENT'
    for h in (5,10,20):
        bridge.observe(rec.forecast_id,h,later(h))
    assert len(o.list_for_forecast(rec.forecast_id))==3
    assert bridge.reconcile()['pilot_outcomes']==3
    pilot.close();f.close();o.close()
