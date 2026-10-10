"""Crash/restart, true DuckDB atomicity, and immutable source evidence regressions."""
from datetime import datetime, timedelta, timezone
import pytest

from birzha.application.prospective_capture import CaptureEvidence, CompletedSessions, AdmissionRefused, ImmutableCollision
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal

T0=datetime(2026,10,10,12,0,tzinfo=timezone.utc)


def forecast(**updates):
    data=dict(forecast_id="atomic_fixture_01",symbol="SBER",secid="SBER",
        created_at_t0="2026-10-10T11:59:30Z",engine_version="TEST_ONLY",
        direction="UP",signal_strength=.2,control="UNKNOWN",route="UNAVAILABLE",
        horizons=tuple(HorizonForecast(h,"UP",.2,None,None) for h in (5,10,20)),
        reasons=("artificial example",),warnings=("no authenticated source",),
        validation_status="UNVALIDATED_BASELINE",reference_price=100.,
        snapshot_id="snap_atomic_fixture",snapshot_contract_version="MARKET_SNAPSHOT_V2")
    data.update(updates)
    return ForecastRecord(**data)


def original(**updates):
    data=dict(source_payload=b"synthetic original provider bytes",
        source_observed_at="2026-10-10T11:59:25Z",
        latest_completed_event_end="2026-10-09T20:00:00Z")
    data.update(updates)
    return CaptureEvidence(**data)


def future(horizon=5, **updates):
    dates=tuple((T0.date()+timedelta(days=i)).isoformat() for i in range(1,horizon+1))
    data=dict(source_payload=b"synthetic future provider bytes",
        source_observed_at=f"{dates[-1]}T20:01:00Z",
        market="SBER",secid="SBER",session_dates=dates,
        expected_calendar_dates=dates,
        candle_completed_at=tuple(f"{d}T20:00:00Z" for d in dates),
        candle_close=tuple(100.+i for i in range(1,horizon+1)))
    data.update(updates)
    return CompletedSessions(**data)


def db(tmp_path, *, now=T0, hook=None):
    return SingleDuckDBProspectiveStagingJournal(tmp_path/"atomic.duckdb",
        staging_root=tmp_path,clock=lambda:now,fault_hook=hook)


@pytest.mark.parametrize("failpoint",["AFTER_RECEIPT","AFTER_FORECAST"])
def test_capture_partial_failure_rolls_back_both_tables(tmp_path,failpoint):
    def fail(where):
        if where==failpoint:
            raise OSError("crash before commit")
    store=db(tmp_path,hook=fail)
    with pytest.raises(OSError,match="crash"):
        store.capture(forecast(),original())
    assert store.audit()["captures"]==0
    store.close()
    store=db(tmp_path)
    assert store.audit()["captures"]==0
    assert store.capture(forecast(),original())["status"]=="APPENDED"
    assert store.audit()["captures"]==1
    store.close()


@pytest.mark.parametrize("failpoint",["AFTER_FUTURE_RECEIPT","AFTER_OUTCOME"])
def test_outcome_partial_failure_rolls_back_both_tables(tmp_path,failpoint):
    store=db(tmp_path)
    store.capture(forecast(),original())
    store.close()
    def fail(where):
        if where==failpoint:
            raise OSError("future crash before commit")
    store=db(tmp_path,now=T0+timedelta(days=30),hook=fail)
    with pytest.raises(OSError,match="future crash"):
        store.observe("atomic_fixture_01",5,future())
    assert store.audit()["outcomes"]==0
    store.close()
    store=db(tmp_path,now=T0+timedelta(days=30))
    assert store.observe("atomic_fixture_01",5,future())["status"]=="APPENDED"
    assert store.audit()["outcomes"]==1
    store.close()


def test_duplicate_receipt_after_restart_preserves_timestamp_and_old_record(tmp_path):
    store=db(tmp_path)
    one=store.capture(forecast(),original())
    store.close()
    store=db(tmp_path,now=T0+timedelta(days=30))
    two=store.capture(forecast(),original())
    assert two["status"]=="DUPLICATE_IDENTICAL"
    assert one["receipt"]==two["receipt"]
    assert store.observe("atomic_fixture_01",5,future())["status"]=="APPENDED"
    assert store.observe("atomic_fixture_01",5,future())["status"]=="DUPLICATE_IDENTICAL"
    store.close()
    canonical=DuckDBForecastJournal(str(tmp_path/"atomic.duckdb"))
    assert canonical.get("atomic_fixture_01").to_dict()==forecast().to_dict()
    assert canonical.count()==1
    canonical.close()
    outcomes=DuckDBOutcomeJournal(str(tmp_path/"atomic.duckdb"))
    assert len(outcomes.list_for_forecast("atomic_fixture_01"))==1
    assert outcomes.list_for_forecast("atomic_fixture_01")[0].target_close==105.0
    outcomes.close()


def test_changed_original_bytes_and_collision_are_rejected(tmp_path):
    store=db(tmp_path)
    store.capture(forecast(),original())
    with pytest.raises(ImmutableCollision):
        store.capture(forecast(),original(source_payload=b"changed"))
    with pytest.raises(ImmutableCollision):
        store.capture(forecast(reference_price=111.),original())
    store.close()
    store=db(tmp_path,now=T0+timedelta(days=30))
    store.observe("atomic_fixture_01",5,future())
    with pytest.raises(ImmutableCollision):
        store.observe("atomic_fixture_01",5,future(candle_close=(101.,102.,103.,104.,106.)))
    assert store.audit()["captures"]==1 and store.audit()["outcomes"]==1
    store.close()


def test_future_outcomes_require_known_exact_source_and_calendar(tmp_path):
    store=db(tmp_path,now=T0+timedelta(days=30))
    with pytest.raises(AdmissionRefused):
        store.observe("atomic_fixture_01",5,future())
    store.close()
    store=db(tmp_path)
    store.capture(forecast(),original())
    store.close()
    store=db(tmp_path,now=T0+timedelta(days=30))
    for bad in (future(secid="different"),
                future(expected_calendar_dates=("2026-10-13",)*5),
                future(source_observed_at="2026-10-11T20:01:00Z")):
        with pytest.raises(AdmissionRefused):
            store.observe("atomic_fixture_01",5,bad)
    assert store.audit()["outcomes"]==0
    store.close()


def test_untrusted_source_wrong_t0_unknown_version_and_path_blocked(tmp_path):
    store=db(tmp_path)
    with pytest.raises(AdmissionRefused):
        store.capture(forecast(),original(source_origin="RECONSTRUCTED"))
    with pytest.raises(AdmissionRefused):
        store.capture(forecast(),original(source_observed_at="2026-10-10T12:02:00Z"))
    with pytest.raises(AdmissionRefused):
        store.capture(forecast(record_version="FORECAST_RECORD_V77"),original())
    assert store.audit()["captures"]==0
    store.close()
    with pytest.raises(AdmissionRefused):
        SingleDuckDBProspectiveStagingJournal(tmp_path.parent/"outside.duckdb",staging_root=tmp_path)
    with pytest.raises(AdmissionRefused):
        SingleDuckDBProspectiveStagingJournal(":memory:",staging_root=tmp_path)


def test_external_corruption_detected_before_any_subsequent_canonical_write(tmp_path):
    store=db(tmp_path)
    store.capture(forecast(),original())
    store.close()
    import duckdb
    conn=duckdb.connect(str(tmp_path/"atomic.duckdb"))
    conn.execute("UPDATE forecast_records SET payload_hash='bad'")
    conn.close()
    store=db(tmp_path)
    with pytest.raises(ImmutableCollision,match="corrupt captured"):
        store.capture(forecast(forecast_id="new_02"),original())
    store.close()


def test_all_three_future_outcomes_one_file_one_canonical_journal(tmp_path):
    store=db(tmp_path)
    store.capture(forecast(),original())
    store.close()
    store=db(tmp_path,now=T0+timedelta(days=30))
    for horizon in (5,10,20):
        assert store.observe("atomic_fixture_01",horizon,future(horizon))["status"]=="APPENDED"
    assert store.audit()["outcomes"]==3
    store.close()
    assert [p.name for p in tmp_path.glob("*.duckdb")]==["atomic.duckdb"]
