"""Offline true-byte /history pages and atomic DuckDB evidence regressions."""
from datetime import datetime, timezone
from hashlib import sha256
import base64
import json

import pytest

from birzha.application.prospective_capture import AdmissionRefused, ImmutableCollision
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from birzha.upstream.capture_scope import bounded_market_capture
from scripts.g2_capture_exact_calendar_history import capture_exact_history
from scripts.g2_capture_and_issue_atomic_staging import capture_and_issue_one
from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
from test_g2_freeze_real_forecast_from_source_receipts import NOW, source_fixture
from test_g2_calendar_time_witness import _witness, _history

STAMP = "2026-10-10T15:00:10Z"


def calendar_witness():
    record = _witness()
    record["pages"][0]["observed_start_utc"] = "2026-10-10T15:00:06Z"
    record["pages"][0]["observed_end_utc"] = STAMP
    return record


def fixtures(tmp_path):
    source = tmp_path/"source"
    stage = tmp_path/"isolated"
    source_fixture(source)
    stage.mkdir()
    return source,stage


def test_original_history_receipts_and_forecast_commit_in_one_duckdb(tmp_path):
    src,root = fixtures(tmp_path)
    witness=calendar_witness()
    issued=issue_atomic_staging(src,root,"SBER",clock=lambda:NOW,calendar_witness=witness)
    assert issued["status"]=="APPENDED"
    assert issued["same_provider_history_witness_persisted"] is True
    store=SingleDuckDBProspectiveStagingJournal(
        root/"SBER.duckdb",staging_root=root,clock=lambda:NOW
    )
    try:
        assert store.audit()["captures"]==1
        packed=store.read_original_calendar_witness(issued["forecast_id"])
        from birzha.storage.g2_calendar_evidence import decode_calendar_witness
        decoded=decode_calendar_witness(packed)
        assert decoded["pages"][0]["raw_body"]==witness["pages"][0]["raw_body"]
        assert decoded["pages"][0]["observed_end_utc"]==STAMP
        assert decoded["secid"]=="SBER"
        assert len(list(root.iterdir()))==1
        assert store.db.execute("SELECT COUNT(*) FROM outcome_records").fetchone()[0]==0
    finally:
        store.close()


def test_changed_calendar_witness_is_immutable_on_duplicate_reissue(tmp_path):
    src,root=fixtures(tmp_path)
    witness=calendar_witness()
    first=issue_atomic_staging(src,root,"SBER",clock=lambda:NOW,calendar_witness=witness)
    second=issue_atomic_staging(src,root,"SBER",clock=lambda:NOW,calendar_witness=witness)
    assert first["forecast_id"]==second["forecast_id"]
    assert second["status"]=="DUPLICATE_IDENTICAL"
    witness["collector_metadata_change"]="must not rewrite original evidence"
    with pytest.raises(ImmutableCollision,match="original calendar witness"):
        issue_atomic_staging(src,root,"SBER",clock=lambda:NOW,calendar_witness=witness)


@pytest.mark.parametrize("failure",["wrong_board","wrong_secid","wrong_date","tampered_page","old_receipt","no_pages","zero_activity"])
def test_contradictory_history_never_writes_forecast(tmp_path,failure):
    src,root=fixtures(tmp_path)
    witness=calendar_witness()
    if failure=="wrong_board":
        witness["board"]="OTHER"
    elif failure=="wrong_secid":
        witness["secid"]="GAZP"
    elif failure=="wrong_date":
        witness["pages"][0]["raw_body"]=_history("2026-10-08")
        witness["pages"][0]["sha256"]=sha256(witness["pages"][0]["raw_body"]).hexdigest()
    elif failure=="tampered_page":
        witness["pages"][0]["sha256"]="0"*64
    elif failure=="old_receipt":
        witness["pages"][0]["observed_end_utc"]="2026-09-01T00:00:00Z"
    elif failure=="no_pages":
        witness["pages"]=[]
    elif failure=="zero_activity":
        witness["pages"][0]["raw_body"]=_history("2026-10-09",0)
        witness["pages"][0]["sha256"]=sha256(witness["pages"][0]["raw_body"]).hexdigest()
    with pytest.raises(AdmissionRefused):
        issue_atomic_staging(src,root,"SBER",clock=lambda:NOW,calendar_witness=witness)
    assert not list(root.glob("*.duckdb"))


def test_tampering_calendar_even_with_rehashed_receipt_is_detected(tmp_path):
    src,root=fixtures(tmp_path)
    result=issue_atomic_staging(src,root,"SBER",clock=lambda:NOW,
                                calendar_witness=calendar_witness())
    store=SingleDuckDBProspectiveStagingJournal(
        root/"SBER.duckdb",staging_root=root,clock=lambda:NOW
    )
    try:
        raw=store.db.execute(
            "SELECT receipt_json FROM g2_atomic_captures WHERE forecast_id=?",
            [result["forecast_id"]],
        ).fetchone()[0]
        receipt=json.loads(raw)
        witness=json.loads(base64.b64decode(receipt["original_history_witness_b64"]))
        witness["pages"][0]["sha256"]="f"*64
        changed=(json.dumps(witness,ensure_ascii=False,sort_keys=True,
                            separators=(",",":"))+"\n").encode()
        receipt["original_history_witness_b64"]=base64.b64encode(changed).decode()
        receipt["original_history_witness_sha256"]=sha256(changed).hexdigest()
        packed=json.dumps(receipt,sort_keys=True,ensure_ascii=False,separators=(",",":"))
        store.db.execute(
            "UPDATE g2_atomic_captures SET receipt_json=?,receipt_sha=? WHERE forecast_id=?",
            [packed,sha256(packed.encode()).hexdigest(),result["forecast_id"]],
        )
        with pytest.raises(ImmutableCollision,match="calendar"):
            store.audit()
    finally:
        store.close()


def test_fault_after_history_receipt_rolls_back_source_and_forecast(tmp_path):
    src,root=fixtures(tmp_path)
    def fault(event):
        if event=="AFTER_RECEIPT":
            raise OSError("synthetic transaction abort")
    with pytest.raises(OSError,match="synthetic transaction abort"):
        issue_atomic_staging(src,root,"SBER",clock=lambda:NOW,
                             calendar_witness=calendar_witness(),fault_hook=fault)
    store=SingleDuckDBProspectiveStagingJournal(
        root/"SBER.duckdb",staging_root=root,clock=lambda:NOW
    )
    try:
        assert store.audit()["captures"]==0
    finally:
        store.close()


def test_history_collector_keeps_original_http_page_bytes_and_cursor():
    original=_history("2026-10-09")
    class Response:
        status_code=200
        headers={}
        body=original
    class Provider:
        def __init__(self):
            self.calls=[]
        def _request(self,path,params):
            self.calls.append((path,params))
            return Response()
    provider=Provider()
    def clock():
        return datetime(2026,10,10,15,0,10,tzinfo=timezone.utc)
    evidence=capture_exact_history(
        provider,{"engine":"stock","market":"shares","board":"TQBR","secid":"SBER"},
        from_day="2026-10-05",through_day="2026-10-09",clock=clock,
    )
    assert len(provider.calls)==1
    assert provider.calls[0][1]["start"]==0
    assert "history.columns" in provider.calls[0][1]
    assert evidence["pages"][0]["raw_body"]==original
    assert evidence["pages"][0]["sha256"]==sha256(original).hexdigest()
    assert evidence["pages"][0]["observed_end_utc"]==STAMP


def test_explicit_history_capture_refuses_without_issuer_on_provider_failure(tmp_path,monkeypatch):
    import scripts.g2_capture_and_issue_atomic_staging as wrapper
    src,root=tmp_path/"raw",tmp_path/"stage"
    root.mkdir()
    calls=[]
    def fake_capture(provider, market_data, path, *, clock, markets):
        source_fixture(path)
        return {
            "verified_complete_market_count":1,
            "failed_markets":[],
            "complete_markets":["SBER"],
            "instruments":[{
                "market":"SBER",
                "instrument":{"engine":"stock","market":"shares","board":"TQBR","secid":"SBER"},
                "timeframes":{"D1":{"requested_from":"2026-06-01","requested_till":"2026-10-09"}},
            }],
        }
    monkeypatch.setattr(wrapper,"capture_all",fake_capture)
    def unavailable(*args,**kw):
        calls.append("history")
        raise ValueError("fake /history access denied")
    monkeypatch.setattr(wrapper,"capture_exact_history",unavailable)
    monkeypatch.setattr(wrapper,"issue_atomic_staging",lambda *a,**kw:pytest.fail(
        "Forecast must not issue without history in required mode"
    ))
    with pytest.raises(ValueError,match="fake /history"):
        capture_and_issue_one(object(),object(),"SBER",src,root,
                              require_history_corroboration=True,clock=lambda:NOW)
    assert calls==["history"]
    assert list(root.iterdir())==[]
