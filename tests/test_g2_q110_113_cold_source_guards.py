"""Q-110..113 adversarial original-source and cold-restart validation.

All databases and inputs are disposable test fixtures, not real MOEX.
"""
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
import sqlite3

import pytest

from birzha.application.g2_future_d1_source_content import (
    FutureD1ContentRefused, replay_native_d1_if_present,
)
from birzha.application.prospective_capture import ImmutableCollision
from test_g2_future_d1_source_integrity import native_d1
from test_prospective_capture import (
    FakeForecastRecord, source as pilot_source, ledger as pilot_ledger,
    BASE,
)
from test_prospective_single_store import (
    forecast, original, future, db as duckdb, T0,
)


def _json(item):
    return json.dumps(item, ensure_ascii=False, sort_keys=True,
                      separators=(",",":"), allow_nan=False)


@pytest.mark.parametrize("bad",[
    b'{"Candles":{"columns":[],"data":[]}}',
    b'{"candles.cursor":{"columns":[],"data":[]}}',
    b'{"CANDLES":{"columns":[],"data":[]}}',
    b'{"candle_data":{"columns":[],"data":[]}}',
])
def test_q110_ambiguous_native_d1_table_name_is_not_opaque_fixture(bad):
    e=future()
    with pytest.raises(FutureD1ContentRefused,match="ambiguous"):
        replay_native_d1_if_present(
            bad,dates=e.session_dates,completed_at=e.candle_completed_at,
            closes=e.candle_close)


def test_q110_legacy_explicit_non_native_synthetic_fixture_stays_unreplayed():
    e=future()
    assert replay_native_d1_if_present(
        e.source_payload,dates=e.session_dates,
        completed_at=e.candle_completed_at,closes=e.candle_close) is False


@pytest.mark.parametrize("offset",["two_days","beyond_24h"])
def test_q111_native_d1_cannot_consume_more_than_one_exchange_day(offset):
    e=future()
    content=json.loads(native_d1(e))
    if offset=="two_days":
        content["candles"]["data"][2][1]="2026-10-15 20:00:00"
    else:
        content["candles"]["data"][2][1]="2026-10-14 11:00:00"
    # Even if a caller changes its claimed session end to match the
    # forged native time, a single D1 cannot span multiple days.
    ends=list(e.candle_completed_at)
    ends[2]=(content["candles"]["data"][2][1].replace(" ","T")+"Z")
    with pytest.raises(FutureD1ContentRefused):
        replay_native_d1_if_present(
            json.dumps(content).encode(),dates=e.session_dates,
            completed_at=tuple(ends),closes=e.candle_close)


@pytest.mark.parametrize("damage",[
    "observed_after_issue","captured_before_issue","changed_snapshot",
    "changed_source_label","forged_db_captured_at",
])
def test_q112_pilot_rejects_rehashed_corrupt_first_receipt_on_cold_audit(tmp_path,damage):
    store=pilot_ledger(tmp_path)
    store.capture(FakeForecastRecord(),pilot_source())
    store.close()
    raw=sqlite3.connect(tmp_path/"pilot.sqlite3")
    raw.execute("DROP TRIGGER captures_no_update")
    receipt=json.loads(raw.execute(
        "SELECT receipt_json FROM captures WHERE forecast_id=?",
        ("fcst_pilot_001",)).fetchone()[0])
    if damage=="observed_after_issue":
        receipt["source_observed_at"]="2026-10-10T12:00:01Z"
    elif damage=="captured_before_issue":
        receipt["captured_at"]="2026-10-10T11:59:00Z"
    elif damage=="changed_snapshot":
        receipt["snapshot_id"]="UNRELATED_SNAPSHOT"
    elif damage=="changed_source_label":
        receipt["source_origin"]="TRUSTED_EXTERNAL_FIRST_RECEIPT"
    elif damage=="forged_db_captured_at":
        raw.execute("UPDATE captures SET captured_at='2026-10-09T11:00:00Z'")
    if damage!="forged_db_captured_at":
        packed=_json(receipt)
        raw.execute("UPDATE captures SET receipt_json=?,receipt_sha=?",
                    (packed,sha256(packed.encode()).hexdigest()))
    raw.commit();raw.close()
    restarted=pilot_ledger(tmp_path,BASE+timedelta(days=1))
    with pytest.raises(ImmutableCollision,match="persisted pilot receipt"):
        restarted.audit()
    restarted.close()


@pytest.mark.parametrize("damage",[
    "receipt_observed_after_t0","receipt_captured_before_t0",
    "receipt_sec_id","receipt_snapshot_id","forecast_column_symbol",
    "forecast_column_t0",
])
def test_q113_duckdb_rejects_rehashed_first_receipt_or_columns_on_restart(
    tmp_path,damage,
):
    store=duckdb(tmp_path)
    store.capture(forecast(),original())
    store.close()
    store=duckdb(tmp_path,now=T0+timedelta(days=1))
    if damage.startswith("forecast_column_"):
        if damage=="forecast_column_symbol":
            store.db.execute("UPDATE forecast_records SET symbol='GAZP'")
        else:
            store.db.execute(
                "UPDATE forecast_records SET created_at_t0='2026-10-01T11:59:30Z'")
    else:
        row=store.db.execute(
            "SELECT receipt_json FROM g2_atomic_captures"
        ).fetchone()[0]
        receipt=json.loads(row)
        if damage=="receipt_observed_after_t0":
            receipt["source_observed_at"]="2026-10-10T12:01:00Z"
        elif damage=="receipt_captured_before_t0":
            receipt["captured_at"]="2026-10-10T11:58:00Z"
        elif damage=="receipt_sec_id":
            receipt["secid"]="GAZP"
        else:
            receipt["snapshot_id"]="wrong_snapshot"
        packed=_json(receipt)
        store.db.execute(
            "UPDATE g2_atomic_captures SET receipt_json=?,receipt_sha=?",
            [packed,sha256(packed.encode()).hexdigest()])
    store.close()
    reopened=duckdb(tmp_path,now=T0+timedelta(days=2))
    with pytest.raises(ImmutableCollision,match="cold source receipt"):
        reopened.audit()
    reopened.close()


def test_q112_and_q113_unmodified_capture_survives_cold_reopen(tmp_path):
    import pathlib
    p=tmp_path/"sqlite";p.mkdir()
    s=pilot_ledger(p); s.capture(FakeForecastRecord(),pilot_source());s.close()
    s=pilot_ledger(p,BASE+timedelta(days=1))
    assert s.audit()["captures"]==1
    s.close()
    d=tmp_path/"duckdb";d.mkdir()
    s=duckdb(d);s.capture(forecast(),original());s.close()
    s=duckdb(d,now=T0+timedelta(days=1))
    assert s.audit()["captures"]==1
    s.close()
