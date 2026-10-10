"""Crash-before-commit, abrupt post-commit restart and cold backup tests.

Every subprocess writes only under pytest's disposable tmp_path on GitHub CI.
An os._exit() is deliberately used IN THE CHILD PROCESS ONLY, so ordinary
Python exception handling and DuckDB.close() cannot mask crash-recovery bugs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from birzha.application.prospective_capture import CaptureEvidence, CompletedSessions
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=30)
KILLED_AT_FAULT = 83


def _forecast():
    return ForecastRecord(
        forecast_id="process_crash_forecast",symbol="SBER",secid="SBER",
        created_at_t0="2026-10-10T11:59:30Z",
        engine_version="SYNTHETIC_CRASH_TEST",direction="UP",
        signal_strength=0.1,control="UNKNOWN",route="UNAVAILABLE",
        horizons=tuple(HorizonForecast(h,"UP",0.1,None,None) for h in (5,10,20)),
        reasons=("disposable-only example",),warnings=(),
        validation_status="UNVALIDATED_BASELINE",reference_price=100.,
        snapshot_id="snapshot_synthetic_crash",snapshot_contract_version="MARKET_SNAPSHOT_V2",
    )


def _source():
    return CaptureEvidence(
        source_payload=b"synthetic provider bytes for crash/restart",
        source_observed_at="2026-10-10T11:59:25Z",
        latest_completed_event_end="2026-10-09T20:00:00Z",
    )


def _future(h=5):
    days=tuple((T0.date()+timedelta(days=i)).isoformat() for i in range(1,h+1))
    return CompletedSessions(
        source_payload=b"synthetic future received D1 bytes",
        source_observed_at=f"{days[-1]}T20:01:00Z",
        market="SBER",secid="SBER",
        session_dates=days,expected_calendar_dates=days,
        candle_completed_at=tuple(f"{x}T20:00:00Z" for x in days),
        candle_close=tuple(float(100+i) for i in range(1,h+1)),
    )


def _store(root: Path, *, later=False, hook=None):
    return SingleDuckDBProspectiveStagingJournal(
        root/"atomic.duckdb",staging_root=root,
        clock=lambda: LATER if later else T0,fault_hook=hook,
    )


def _child_process(root: Path, operation: str, failpoint: str = "none"):
    """Execute this same test file as a separate process, never on the pytest host."""
    return subprocess.run(
        [sys.executable,str(Path(__file__).resolve()),str(root),operation,failpoint],
        capture_output=True,text=True,timeout=25,check=False,
    )


def _child_worker(root: Path, operation: str, failpoint: str) -> None:
    def kill_at_selected_point(event: str):
        if event==failpoint:
            # A separate marker proves the child actually reached the requested
            # halfway point; it does NOT touch or flush the DuckDB transaction.
            (root/"fault_reached.txt").write_text(event,encoding="utf-8")
            os._exit(KILLED_AT_FAULT)

    if operation=="open_only":
        store=_store(root,later=True)
        store.audit()
        store.close()
        return

    store=_store(root,later=operation in {"crash_outcome","commit_outcome"},
                 hook=kill_at_selected_point)
    if operation in {"crash_capture","commit_capture"}:
        store.capture(_forecast(),_source())
    elif operation in {"crash_outcome","commit_outcome"}:
        store.observe("process_crash_forecast",5,_future())
    else:
        raise RuntimeError("unexpected test-only operation")
    if operation.startswith("commit_"):
        # Immediately exit without store.close(). Commit already returned.
        os._exit(0)
    raise AssertionError("selected faultpoint was not reached")


@pytest.mark.parametrize("fault",["AFTER_RECEIPT","AFTER_FORECAST"])
def test_process_exits_mid_capture_and_reopens_without_half_record(tmp_path, fault):
    result=_child_process(tmp_path,"crash_capture",fault)
    assert result.returncode==KILLED_AT_FAULT,(result.stdout,result.stderr)
    assert (tmp_path/"fault_reached.txt").read_text(encoding="utf-8")==fault
    store=_store(tmp_path)
    assert store.audit()=={"schema":"G2_ATOMIC_SINGLE_DUCKDB_STAGING_V2",
                           "captures":0,"outcomes":0,"staging_only":True}
    assert store.capture(_forecast(),_source())["status"]=="APPENDED"
    store.close()
    reopened=_store(tmp_path)
    assert reopened.audit()["captures"]==1
    reopened.close()


@pytest.mark.parametrize("fault",["AFTER_FUTURE_RECEIPT","AFTER_OUTCOME"])
def test_process_exits_mid_outcome_without_orphan(tmp_path,fault):
    store=_store(tmp_path)
    store.capture(_forecast(),_source())
    store.close()
    result=_child_process(tmp_path,"crash_outcome",fault)
    assert result.returncode==KILLED_AT_FAULT,(result.stdout,result.stderr)
    assert (tmp_path/"fault_reached.txt").read_text(encoding="utf-8")==fault
    reopened=_store(tmp_path,later=True)
    assert reopened.audit()["captures"]==1
    assert reopened.audit()["outcomes"]==0
    assert reopened.observe("process_crash_forecast",5,_future())["status"]=="APPENDED"
    reopened.close()
    recovered=_store(tmp_path,later=True)
    assert recovered.audit()["outcomes"]==1
    recovered.close()


def test_committed_forecast_survives_process_exit_without_close(tmp_path):
    result=_child_process(tmp_path,"commit_capture")
    assert result.returncode==0,(result.stdout,result.stderr)
    store=_store(tmp_path,later=True)
    assert store.audit()["captures"]==1
    assert store.capture(_forecast(),_source())["status"]=="DUPLICATE_IDENTICAL"
    store.close()


def test_committed_outcome_survives_process_exit_without_close(tmp_path):
    store=_store(tmp_path)
    store.capture(_forecast(),_source())
    store.close()
    result=_child_process(tmp_path,"commit_outcome")
    assert result.returncode==0,(result.stdout,result.stderr)
    reopened=_store(tmp_path,later=True)
    assert reopened.audit()["outcomes"]==1
    assert reopened.observe("process_crash_forecast",5,_future())["status"]=="DUPLICATE_IDENTICAL"
    reopened.close()


def test_second_process_refused_while_single_writer_owns_file(tmp_path):
    primary=_store(tmp_path)
    primary.capture(_forecast(),_source())
    secondary=_child_process(tmp_path,"open_only")
    assert secondary.returncode!=0, "two independent processes must not silently write same DuckDB"
    assert primary.audit()["captures"]==1
    primary.close()
    secondary_after_close=_child_process(tmp_path,"open_only")
    assert secondary_after_close.returncode==0,secondary_after_close.stderr


def _digest(path: Path):
    return sha256(path.read_bytes()).hexdigest()


def test_cold_backup_restore_preserves_receipts_and_future_evidence(tmp_path):
    store=_store(tmp_path)
    first=store.capture(_forecast(),_source())
    store.close()
    store=_store(tmp_path,later=True)
    outcome=store.observe("process_crash_forecast",5,_future())["outcome"]
    store.close()

    original=tmp_path/"atomic.duckdb"
    backup=tmp_path/"offline_backup"/"atomic.duckdb"
    backup.parent.mkdir()
    # Offline-only snapshot: copy after closing every writer. No hot file copy.
    shutil.copy2(original,backup)
    assert _digest(original)==_digest(backup)

    restore_root=tmp_path/"restored_offline"
    restore_root.mkdir()
    restored_path=restore_root/"atomic.duckdb"
    shutil.copy2(backup,restored_path)
    assert _digest(restored_path)==_digest(backup)

    restored=_store(restore_root,later=True)
    assert restored.audit()["captures"]==1
    assert restored.audit()["outcomes"]==1
    assert restored.capture(_forecast(),_source())["receipt"]==first["receipt"]
    assert restored.observe("process_crash_forecast",5,_future())["outcome"].to_dict()["target_close"]==outcome.target_close
    assert restored.observe("process_crash_forecast",10,_future(10))["status"]=="APPENDED"
    restored.close()

    # Restored database can advance independently and never modifies source.
    source=_store(tmp_path,later=True)
    assert source.audit()["outcomes"]==1
    source.close()


def test_backup_copies_of_corrupted_canonical_hash_fail_readback(tmp_path):
    store=_store(tmp_path)
    store.capture(_forecast(),_source())
    store.close()
    import duckdb
    conn=duckdb.connect(str(tmp_path/"atomic.duckdb"))
    conn.execute("UPDATE forecast_records SET payload_hash='corrupted'")
    conn.close()
    backup_root=tmp_path/"bad_restore"
    backup_root.mkdir()
    shutil.copy2(tmp_path/"atomic.duckdb", backup_root/"atomic.duckdb")
    recovered=_store(backup_root)
    from birzha.application.prospective_capture import ImmutableCollision
    with pytest.raises(ImmutableCollision,match="corrupt"):
        recovered.audit()
    recovered.close()


if __name__=="__main__":
    # Only a subprocess gets here. Parent pytest must never call os._exit.
    _child_worker(Path(sys.argv[1]),sys.argv[2],sys.argv[3])
