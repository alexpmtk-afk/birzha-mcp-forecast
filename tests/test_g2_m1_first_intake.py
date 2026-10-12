"""Q-090 isolated single-original-page LOCAL receipt event tests, no MOEX."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import sqlite3

import pytest

from birzha.application.g2_m1_first_intake import (
    OriginalM1LocalIntake, LocalIntakeRefused, LocalIntakeCollision,
)
from test_g2_m1_m15_source_replay import make_page, candidate, inst, rows
from test_g2_m15_staging_preflight import notice

AT=datetime(2026,10,9,7,16,7,tzinfo=timezone.utc)

class Clock:
    now=AT
    def __call__(self): return self.now

def opened(tmp_path,clock=None):
    return OriginalM1LocalIntake(
        tmp_path/"g2_m1_receipts_scratch.sqlite3",
        staging_root=tmp_path,clock=clock or Clock())

def append(j,p=None,key="TEST_CAPTURE"):
    return j.append(capture_key=key,instrument=inst(),
                    page=p or make_page())

def test_first_local_receipt_is_committed_once_and_replayed_after_restart(tmp_path):
    clock=Clock()
    with opened(tmp_path,clock) as j:
        result=append(j)
        assert result["status"]=="NEW_LOCAL_RECEIPT"
        assert result["first_local_received_utc"]=="2026-10-09T07:16:07Z"
        assert result["independently_attested"] is False
        first=j.audit()
        same=append(j)
        assert same["status"]=="DUPLICATE_IDENTICAL" and same["new_receipts"]==0
        assert same["first_local_received_utc"]==result["first_local_received_utc"]
    clock.now+=timedelta(hours=24)
    with opened(tmp_path,clock) as j:
        retry=append(j)
        assert retry["status"]=="DUPLICATE_IDENTICAL"
        assert j.audit()==first
        assert j.pages(capture_key="TEST_CAPTURE",instrument=inst())==(make_page(),)

def test_handoff_binds_retained_original_bytes_to_offline_q088_only(tmp_path):
    with opened(tmp_path) as j:
        append(j)
        result=j.original_capture_handoff(
            capture_key="TEST_CAPTURE",instrument=inst(),
            candidate=candidate(),session_notice=notice(),
            evaluated_at=AT+timedelta(seconds=5))
        assert result["q088"]["commitment_sha256"]==(
            result["manifest"]["q088_original_commitment_sha256"])
        assert result["manifest"]["local_first_received_at_utc"]=="2026-10-09T07:16:07Z"
        assert result["manifest"]["original_receipts"][0]["raw_sha256"]==make_page().raw_sha256
        assert sha256(result["handoff_bytes"]).hexdigest()==result["handoff_sha256"]
        assert result["new_writes"]==0
        assert result["manifest"]["first_actual_upstream_delivery_independently_proven"] is False
        assert result["level_journal_admission_authorized"] is False
        assert result["production_authorized"] is False
        assert j.audit()["receipt_count"]==1

def test_changed_original_bytes_or_metadata_cannot_overwrite_first_receipt(tmp_path):
    with opened(tmp_path) as j:
        append(j)
        before=j.audit()
        p=make_page()
        changed=p.raw_body+b" "
        with pytest.raises(LocalIntakeCollision):
            append(j,replace(p,raw_body=changed,
                             raw_sha256=sha256(changed).hexdigest()))
        with pytest.raises(LocalIntakeCollision):
            append(j,replace(p,content_type="text/plain"))
        assert j.audit()==before

@pytest.mark.parametrize("change",[
    "missing_bytes","wrong_sha","future","stale","naive","wrong_offset",
    "wrong_instrument","bad_key","missing_root",
])
def test_reject_untrusted_or_stale_first_intake(tmp_path,change):
    c=Clock();p=make_page()
    if change=="missing_root":
        with pytest.raises(LocalIntakeRefused):
            OriginalM1LocalIntake(tmp_path/"not-created"/"g2_m1_receipts_x.sqlite3",
                                  staging_root=tmp_path/"not-created")
        return
    if change=="future":p=replace(p,response_received_utc="2026-10-09T07:17:00Z")
    if change=="stale":c.now+=timedelta(minutes=10)
    if change=="naive":p=replace(p,response_received_utc="2026-10-09T07:16:05")
    if change=="missing_bytes":p=replace(p,raw_body=b"",raw_sha256=sha256(b"").hexdigest())
    if change=="wrong_sha":p=replace(p,raw_sha256="0"*64)
    if change=="wrong_offset":p=replace(p,start=10)
    with opened(tmp_path,c) as j:
        before=j.audit()
        with pytest.raises((LocalIntakeRefused,TypeError)):
            j.append(capture_key="bad key" if change=="bad_key" else "CASE",
                     instrument=replace(inst(),secid="") if change=="wrong_instrument" else inst(),
                     page=p)
        assert j.audit()==before

def test_capture_page_revisions_require_new_capture_and_keep_events_isolated(tmp_path):
    with opened(tmp_path) as j:
        first=append(j,key="FIRST")
        assert first["status"]=="NEW_LOCAL_RECEIPT"
        again=append(j,key="SECOND")
        assert again["status"]=="NEW_LOCAL_RECEIPT"
        assert j.audit()["receipt_count"]==2
        assert len(j.pages(capture_key="FIRST",instrument=inst()))==1
        assert len(j.pages(capture_key="SECOND",instrument=inst()))==1

def test_multi_page_original_capture_handoff_uses_both_first_receipts(tmp_path):
    a=rows()
    first=make_page(a[:8],start=0,total=15,page_size=8)
    last=make_page(a[8:],start=8,total=15,page_size=8,
                   first="2026-10-09T07:16:05Z",last="2026-10-09T07:16:08Z")
    clock=Clock()
    with opened(tmp_path,clock) as j:
        append(j,first)
        clock.now=AT+timedelta(seconds=2)
        append(j,last)
        h=j.original_capture_handoff(
            capture_key="TEST_CAPTURE",instrument=inst(),
            candidate=candidate(),session_notice=notice(),
            evaluated_at=AT+timedelta(seconds=5))
        assert len(h["manifest"]["original_receipts"])==2
        assert h["q088"]["source_page_count"]==2

def test_append_only_triggers_and_unprotected_timestamp_corruption_detected(tmp_path):
    clock=Clock()
    path=tmp_path/"g2_m1_receipts_scratch.sqlite3"
    with opened(tmp_path,clock) as j:
        append(j)
        with pytest.raises(sqlite3.DatabaseError):
            j.db.execute("UPDATE m1_local_receipts SET local_received_utc='fake'")
        with pytest.raises(sqlite3.DatabaseError):
            j.db.execute("DELETE FROM m1_local_receipts")
    db=sqlite3.connect(path)
    db.execute("DROP TRIGGER m1_local_no_update")
    db.execute("UPDATE m1_local_receipts SET local_received_utc='2040-01-01T00:00:00Z'")
    db.commit();db.close()
    with pytest.raises(LocalIntakeRefused): opened(tmp_path,clock)
    db=sqlite3.connect(path)
    db.execute("""CREATE TRIGGER m1_local_no_update
        BEFORE UPDATE ON m1_local_receipts
        BEGIN SELECT RAISE(ABORT,'append-only M1 receipt'); END""")
    db.commit();db.close()
    with pytest.raises(LocalIntakeCollision):opened(tmp_path,clock)

def test_handoff_refuses_corrupted_source_or_false_calendar(tmp_path):
    with opened(tmp_path) as j:
        append(j)
        with pytest.raises(LocalIntakeRefused):
            j.original_capture_handoff(
                capture_key="TEST_CAPTURE",instrument=inst(),
                candidate=replace(candidate(),volume=9999),
                session_notice=notice(),evaluated_at=AT+timedelta(seconds=5))
        with pytest.raises(LocalIntakeRefused):
            j.original_capture_handoff(
                capture_key="TEST_CAPTURE",instrument=inst(),candidate=candidate(),
                session_notice=replace(notice(),secid="GAZP"),
                evaluated_at=AT+timedelta(seconds=5))
        assert j.audit()["receipt_count"]==1
