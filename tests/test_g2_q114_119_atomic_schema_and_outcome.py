"""Q114..Q119: disposable schema-crash and forensic Outcome NO-GO fixtures."""
from datetime import timedelta
from hashlib import sha256
import json
import os
import sqlite3
import subprocess
import sys

import duckdb
import pytest

from birzha.application.prospective_capture import ImmutableCollision
from birzha.storage.prospective_single_store import (
    SingleDuckDBProspectiveStagingJournal,
)
from test_prospective_capture import (
    FakeForecastRecord, source as pilot_source,
    future as pilot_future, ledger, BASE,
)
from test_prospective_single_store import (
    db, forecast, original, future, T0,
)


def _canonical(d):
    return json.dumps(d, sort_keys=True,separators=(",",":"),
                      ensure_ascii=False,allow_nan=False)


def test_q114_hard_exit_during_first_duckdb_schema_reopens_empty(tmp_path):
    # A real os._exit is performed only in an isolated child process, on
    # a CI temporary directory, without HOME and production processes.
    child=r"""
import os,sys
from pathlib import Path
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
root=Path(sys.argv[1])
def kill(event):
    if event=="DURING_INITIAL_SCHEMA":
        os._exit(74)
SingleDuckDBProspectiveStagingJournal(root/"atomic.duckdb",
    staging_root=root,fault_hook=kill)
raise AssertionError("expected schema crash")
"""
    environment=os.environ.copy()
    environment["PYTHONPATH"]=os.pathsep.join(x for x in sys.path if x)
    done=subprocess.run([sys.executable,"-c",child,str(tmp_path)],
                        env=environment,capture_output=True,text=True,timeout=25)
    assert done.returncode==74,(done.returncode,done.stderr[-1200:])
    store=db(tmp_path)
    assert store.audit()["captures"]==0
    assert store.capture(forecast(),original())["status"]=="APPENDED"
    store.close()
    reopened=db(tmp_path)
    assert reopened.audit()["captures"]==1
    reopened.close()


@pytest.mark.parametrize("partial",["meta_only","four_of_seven","alien_table"])
def test_q115_partial_or_unexpected_duckdb_schema_fails_without_self_repair(tmp_path,partial):
    path=tmp_path/"atomic.duckdb"
    con=duckdb.connect(str(path))
    con.execute("CREATE TABLE g2_atomic_meta(version VARCHAR PRIMARY KEY)")
    con.execute("INSERT INTO g2_atomic_meta VALUES ('G2_ATOMIC_SINGLE_DUCKDB_STAGING_V2')")
    if partial=="four_of_seven":
        con.execute("CREATE TABLE forecast_records(forecast_id VARCHAR PRIMARY KEY)")
        con.execute("CREATE TABLE outcome_records(outcome_id VARCHAR PRIMARY KEY)")
        con.execute("CREATE TABLE g2_atomic_captures(forecast_id VARCHAR PRIMARY KEY)")
    elif partial=="alien_table":
        con.execute("CREATE TABLE unexplained_payload(x INT)")
    con.close()
    with pytest.raises(ImmutableCollision,match="partial or unexpected staging schema"):
        db(tmp_path)
    reopened=duckdb.connect(str(path),read_only=True)
    tables={r[0] for r in reopened.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
    assert len(tables)==(4 if partial=="four_of_seven" else
                         2 if partial=="alien_table" else 1)
    assert "g2_atomic_futures" not in tables
    reopened.close()


@pytest.mark.parametrize("field,new",[
    ("forecast_id","NOT_ORIGINAL"),
    ("horizon_sessions",20),
])
def test_q116_duckdb_outcome_denormalized_columns_corruption_detected_after_reopen(
    tmp_path,field,new,
):
    store=db(tmp_path)
    store.capture(forecast(),original());store.close()
    store=db(tmp_path,now=T0+timedelta(days=30))
    store.observe("atomic_fixture_01",5,future())
    store.db.execute(f"UPDATE outcome_records SET {field}=?",[new])
    store.close()
    restarted=db(tmp_path,now=T0+timedelta(days=30))
    with pytest.raises(ImmutableCollision,
                       match="canonical Outcome identity columns"):
        restarted.audit()
    restarted.close()


@pytest.mark.parametrize("damage",[
    "wrong_market","wrong_secid","wrong_horizon",
    "wrong_reference","fake_return","fake_hit","before_observed",
    "observed_before_capture","wrong_capture_timestamp",
    "incorrect_pilot_flag",
])
def test_q117_118_pilot_rehashed_outcome_facts_rejected_after_restart(tmp_path,damage):
    s=ledger(tmp_path)
    s.capture(FakeForecastRecord(),pilot_source());s.close()
    s=ledger(tmp_path,BASE+timedelta(days=30))
    s.observe("fcst_pilot_001",5,pilot_future());s.close()
    dbconn=sqlite3.connect(tmp_path/"pilot.sqlite3")
    dbconn.execute("DROP TRIGGER outcomes_no_update")
    result=json.loads(dbconn.execute("SELECT payload_json FROM outcomes").fetchone()[0])
    if damage=="wrong_market":result["market"]="GAZP"
    elif damage=="wrong_secid":result["secid"]="GAZP"
    elif damage=="wrong_horizon":result["horizon_sessions"]=10
    elif damage=="wrong_reference":result["reference_price"]=200.0
    elif damage=="fake_return":result["actual_return_pct"]=999.0
    elif damage=="fake_hit":result["direction_hit"]=False
    elif damage=="before_observed":
        result["outcome_written_at"]="2026-10-09T12:00:00Z"
    elif damage=="observed_before_capture":
        result["source_observed_at"]="2026-10-10T11:58:00Z"
    elif damage=="wrong_capture_timestamp":
        result["capture_at"]="2026-10-10T11:59:30Z"
    elif damage=="incorrect_pilot_flag":result["pilot_only"]=False
    modified=_canonical(result)
    dbconn.execute("UPDATE outcomes SET payload_json=?,payload_sha=?",
                   (modified,sha256(modified.encode()).hexdigest()))
    dbconn.commit();dbconn.close()
    reopened=ledger(tmp_path,BASE+timedelta(days=30))
    with pytest.raises(ImmutableCollision):
        reopened.audit()
    reopened.close()


def test_q117_118_unaltered_outcome_is_valid_on_reopen(tmp_path):
    s=ledger(tmp_path);s.capture(FakeForecastRecord(),pilot_source());s.close()
    s=ledger(tmp_path,BASE+timedelta(days=30))
    s.observe("fcst_pilot_001",5,pilot_future());s.close()
    s=ledger(tmp_path,BASE+timedelta(days=30))
    assert s.audit()["outcomes"]==1
    s.close()


def test_q119_research_quality_does_not_upgrade_unverified_release_state():
    from pathlib import Path
    from scripts.g2_release_gate import validate
    manifest=json.loads((Path(__file__).resolve().parents[1] /
          "docs/G2_RELEASE_GATE_20261010.json").read_text(encoding="utf-8"))
    verdict=validate(manifest)
    assert verdict["release_allowed"] is False
    assert len(verdict["blocking_requirements"])==12
    assert manifest["user_approved_main_merge"] is False
    assert manifest["user_approved_home_deploy"] is False
    assert manifest["release_gates"]["future_outcomes_5_10_20"]["status"]=="BLOCKED"
    assert manifest["release_gates"]["historical_first_receipt_attestation"]["status"]=="BLOCKED"
