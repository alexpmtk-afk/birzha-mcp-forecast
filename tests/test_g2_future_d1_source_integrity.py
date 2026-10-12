"""Q-102..Q-105: native source replay, provenance, chronology, crash tests."""
import json
from datetime import timedelta
from dataclasses import replace
from hashlib import sha256
import os
import sqlite3
import subprocess
import sys
import pytest

from birzha.application.g2_future_d1_source_content import (
    replay_native_d1_if_present, FutureD1ContentRefused,
)
from birzha.application.prospective_capture import (
    ProspectivePilotLedger, AdmissionRefused, ImmutableCollision,
)
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from test_prospective_capture import (
    FakeForecastRecord, source as pilot_source, future as pilot_future, ledger, BASE,
)
from test_prospective_single_store import (
    forecast, original, future as duck_future, db, T0,
)


def native_d1(evidence):
    # Synthetic illustration of a raw ISS D1 page, not an authenticated
    # or actually fetched exchange response.
    return json.dumps({
        "candles":{
            "columns":["begin","end","close","open","high","low","volume","value"],
            "data":[[
                f"{day} 10:00:00",f"{day} 23:00:00",
                price, price, price+0.25, price-0.25, 100.0, price*100.0
            ] for day,price in zip(
                evidence.session_dates,evidence.candle_close)]
        }
    },sort_keys=True,separators=(",",":")).encode()


def test_native_source_complete_replay_is_content_only():
    ev=duck_future()
    assert replay_native_d1_if_present(
        native_d1(ev), dates=ev.session_dates,
        completed_at=ev.candle_completed_at,closes=ev.candle_close) is True
    assert replay_native_d1_if_present(
        ev.source_payload,dates=ev.session_dates,
        completed_at=ev.candle_completed_at,closes=ev.candle_close) is False


@pytest.mark.parametrize("bad",[
    "wrong_close","wrong_time","extra","missing","same_day_twice",
    "duplicate_json_key","bad_close","bad_row",
    "high_below_close","low_above_open","negative_volume",
    "negative_value","boolean_close","partial_native_columns",
    "malformed_json_escaped_candle",
])
def test_native_original_source_mismatch_refused(bad):
    ev=duck_future()
    doc=json.loads(native_d1(ev))
    if bad=="wrong_close":
        doc["candles"]["data"][1][2]=999.0
    elif bad=="wrong_time":
        doc["candles"]["data"][3][1]="2026-10-14 21:00:00"
    elif bad=="extra":
        doc["candles"]["data"].append(doc["candles"]["data"][-1][:])
    elif bad=="missing":
        doc["candles"]["data"].pop()
    elif bad=="same_day_twice":
        doc["candles"]["data"][1][0]=doc["candles"]["data"][0][0]
    elif bad=="bad_close":
        doc["candles"]["data"][0][2]=0.0
    elif bad=="bad_row":
        doc["candles"]["data"][0].pop()
    elif bad=="high_below_close":
        doc["candles"]["data"][0][4]=doc["candles"]["data"][0][2]-1
    elif bad=="low_above_open":
        doc["candles"]["data"][0][5]=doc["candles"]["data"][0][3]+1
    elif bad=="negative_volume":
        doc["candles"]["data"][0][6]=-1
    elif bad=="negative_value":
        doc["candles"]["data"][0][7]=-10
    elif bad=="boolean_close":
        doc["candles"]["data"][0][2]=True
    elif bad=="partial_native_columns":
        doc["candles"]["columns"]=["begin","end","close"]
        doc["candles"]["data"]=[v[:3] for v in doc["candles"]["data"]]
    raw=(b'{"candles":{},"candles":{}}' if bad=="duplicate_json_key"
         else b'{"\\u0063andles":' if bad=="malformed_json_escaped_candle"
         else json.dumps(doc).encode())
    with pytest.raises(FutureD1ContentRefused):
        replay_native_d1_if_present(raw,dates=ev.session_dates,
                                    completed_at=ev.candle_completed_at,
                                    closes=ev.candle_close)


def test_isolated_duckdb_original_iss_content_replayed_without_provider_certification(tmp_path):
    store=db(tmp_path)
    assert store.capture(forecast(),original())["status"]=="APPENDED"
    store.close()
    ev=duck_future()
    store=db(tmp_path,now=T0+timedelta(days=30))
    result=store.observe("atomic_fixture_01",5,
                         replace(ev,source_payload=native_d1(ev)))
    assert result["status"]=="APPENDED"
    assert result["outcome"].status=="OBSERVED"
    assert store.audit()["outcomes"]==1
    saved=json.loads(store.db.execute(
        "SELECT evidence_json FROM g2_atomic_futures").fetchone()[0])
    inner=saved["future_input_evidence"]
    assert inner["native_d1_original_rows_match_claims"] is True
    assert inner["source_transport_independently_authenticated"] is False
    assert inner["calendar_independently_authenticated"] is False
    store.close()
    reopened=db(tmp_path,now=T0+timedelta(days=30))
    assert reopened.audit()["outcomes"]==1
    reopened.close()


def test_duckdb_refuses_native_iss_source_that_disagrees_with_claim(tmp_path):
    store=db(tmp_path)
    store.capture(forecast(),original())
    store.close()
    ev=duck_future()
    doc=json.loads(native_d1(ev));doc["candles"]["data"][2][2]=999.0
    raw=json.dumps(doc).encode()
    store=db(tmp_path,now=T0+timedelta(days=30))
    with pytest.raises(AdmissionRefused,match="original native D1"):
        store.observe("atomic_fixture_01",5,replace(ev,source_payload=raw))
    assert store.audit()["outcomes"]==0
    store.close()


def test_sqlite_pilot_native_source_replay_and_provenance_false(tmp_path):
    p=ledger(tmp_path)
    p.capture(FakeForecastRecord(),pilot_source())
    p.close()
    ev=pilot_future()
    p=ledger(tmp_path,BASE+timedelta(days=30))
    got=p.observe("fcst_pilot_001",5,replace(ev,source_payload=native_d1(ev)))
    assert got["status"]=="APPENDED"
    assert got["outcome"]["native_d1_original_rows_match_claims"] is True
    assert got["outcome"]["source_transport_independently_authenticated"] is False
    assert got["outcome"]["calendar_independently_authenticated"] is False
    assert p.audit()["outcomes"]==1
    p.close()
    reopened=ledger(tmp_path,BASE+timedelta(days=30))
    assert reopened.audit()["outcomes"]==1
    reopened.close()


@pytest.mark.parametrize("mode",["late_day","equal_timestamps"])
def test_pilot_future_completion_must_match_actual_moscow_day_and_strict_order(tmp_path,mode):
    p=ledger(tmp_path)
    p.capture(FakeForecastRecord(),pilot_source())
    p.close()
    ev=pilot_future()
    if mode=="late_day":
        altered=list(ev.candle_completed_at)
        altered[0]="2026-10-14T20:00:00Z"
        observed="2026-10-17T20:01:00Z"
    else:
        altered=list(ev.candle_completed_at)
        altered[1]=altered[0]
        observed=ev.source_observed_at
    p=ledger(tmp_path,BASE+timedelta(days=30))
    with pytest.raises(AdmissionRefused,match="uncompleted|strictly ordered"):
        p.observe("fcst_pilot_001",5,replace(
            ev,candle_completed_at=tuple(altered),source_observed_at=observed))
    assert p.audit()["outcomes"]==0
    p.close()


def test_pilot_rejects_forged_calendar_authenticity_even_after_rehash(tmp_path):
    p=ledger(tmp_path)
    p.capture(FakeForecastRecord(),pilot_source())
    p.close()
    p=ledger(tmp_path,BASE+timedelta(days=30))
    p.observe("fcst_pilot_001",5,pilot_future())
    p.close()
    con=sqlite3.connect(tmp_path/"pilot.sqlite3")
    con.execute("DROP TRIGGER outcomes_no_update")
    row=json.loads(con.execute("SELECT payload_json FROM outcomes").fetchone()[0])
    row["calendar_independently_authenticated"]=True
    enc=json.dumps(row,sort_keys=True,ensure_ascii=False,
                   separators=(",",":"),allow_nan=False)
    con.execute("UPDATE outcomes SET payload_json=?,payload_sha=?",
                (enc,sha256(enc.encode()).hexdigest()))
    con.commit();con.close()
    p=ledger(tmp_path,BASE+timedelta(days=30))
    with pytest.raises(ImmutableCollision,match="falsely certified"):
        p.audit()
    p.close()


def test_sqlite_pilot_partial_schema_is_rolled_back_after_actual_process_exit(tmp_path):
    # Test subprocess only, fully disposable directory, no HOME/production.
    child=r'''
import os,sqlite3,sys
from pathlib import Path
from birzha.application.prospective_capture import ProspectivePilotLedger
root=Path(sys.argv[1]);path=root/"pilot.sqlite3"
base=sqlite3.connect
def modified(*args,**kwargs):
    conn=base(*args,**kwargs)
    def trace(sql):
        if "CREATE TRIGGER IF NOT EXISTS outcomes_no_delete" in sql:
            os._exit(71)
    conn.set_trace_callback(trace)
    return conn
sqlite3.connect=modified
ProspectivePilotLedger(path)
sys.exit(93)
'''
    env=os.environ.copy()
    env["PYTHONPATH"]=os.pathsep.join(x for x in sys.path if x)
    run=subprocess.run([sys.executable,"-c",child,str(tmp_path)],
                       env=env,capture_output=True,text=True,timeout=20)
    assert run.returncode==71,(run.returncode,run.stderr[-1000:])
    p=ledger(tmp_path)
    assert p.audit()["captures"]==0
    assert p._db.execute("PRAGMA synchronous").fetchone()==(2,)
    assert p.capture(FakeForecastRecord(),pilot_source())["status"]=="APPENDED"
    assert p.audit()["captures"]==1
    p.close()
