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


def test_handoff_cannot_precede_later_local_original_page_commit(tmp_path):
    all_rows=rows()
    first=make_page(all_rows[:8],start=0,total=15,page_size=8)
    second=make_page(all_rows[8:],start=8,total=15,page_size=8,
                     first="2026-10-09T07:16:05Z",
                     last="2026-10-09T07:16:08Z")
    clock=Clock()
    with opened(tmp_path,clock) as j:
        append(j,first)
        clock.now=AT+timedelta(seconds=4)  # second local commit is 07:16:11
        append(j,second)
        assert j.audit()["receipt_count"]==2
        # Every caller-reported HTTP receipt precedes this evaluation,
        # but the second *local* receipt does not. Old code accepted it.
        with pytest.raises(LocalIntakeRefused,
                           match="evaluation predates a locally received page"):
            j.original_capture_handoff(
                capture_key="TEST_CAPTURE",instrument=inst(),
                candidate=candidate(),session_notice=notice(),
                evaluated_at=AT+timedelta(seconds=2))
        allowed=j.original_capture_handoff(
            capture_key="TEST_CAPTURE",instrument=inst(),
            candidate=candidate(),session_notice=notice(),
            evaluated_at=AT+timedelta(seconds=5))
        assert len(allowed["manifest"]["original_receipts"])==2
        assert allowed["level_journal_admission_authorized"] is False


def _killed_child(tmp_path, mode):
    """Hard-exit ONLY a disposable subprocess, never the pytest process."""
    import os
    import subprocess
    import sys

    (tmp_path/"crash_raw.json").write_bytes(make_page().raw_body)
    child=r'''
import os, sqlite3, sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from birzha.application import g2_m1_first_intake as mod
from birzha.application.g2_m1_m15_source_replay import OriginalM1Page
from birzha.domain.market import Instrument

root=Path(sys.argv[1])
mode=sys.argv[2]
path=root/"g2_m1_receipts_scratch.sqlite3"
if mode=="schema":
    old=sqlite3.connect
    def patched(*args,**kwargs):
        connection=old(*args,**kwargs)
        def trace(sql):
            if "CREATE TRIGGER IF NOT EXISTS m1_local_no_delete" in sql:
                os._exit(37)  # atomic initialization must roll back
        connection.set_trace_callback(trace)
        return connection
    mod.sqlite3.connect=patched
elif mode=="append":
    old=sqlite3.connect
    class CrashConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql=="COMMIT":
                os._exit(37)  # row inserted; transaction not committed
            return super().execute(sql,parameters)
    mod.sqlite3.connect=lambda *a,**kw: old(*a,**kw,factory=CrashConnection)
raw=(root/"crash_raw.json").read_bytes()
page=OriginalM1Page(
    raw_body=raw, raw_sha256=sha256(raw).hexdigest(),
    request_url="https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/SBER/candles.json?interval=1&start=0&from=2026-10-09&till=2026-10-09",
    http_status=200,content_type="application/json",
    request_started_utc="2026-10-09T07:16:00Z",
    response_received_utc="2026-10-09T07:16:05Z",
    start=0)
inst=Instrument(symbol="SBER",secid="SBER",board="TQBR",
                engine="stock",market="shares",asset_class="equity")
with mod.OriginalM1LocalIntake(
    path,staging_root=root,
    clock=lambda:datetime(2026,10,9,7,16,7,tzinfo=timezone.utc)) as j:
    j.append(capture_key="CRASH",instrument=inst,page=page)
sys.exit(88)  # should not arrive here
'''
    env=os.environ.copy()
    env["PYTHONPATH"]=os.pathsep.join(x for x in sys.path if x)
    done=subprocess.run([sys.executable,"-c",child,str(tmp_path),mode],
                        env=env,capture_output=True,text=True,timeout=15)
    assert done.returncode==37, (done.returncode,done.stderr[-1400:])


def test_uncommitted_first_receipt_is_rolled_back_after_hard_process_exit(tmp_path):
    with opened(tmp_path) as j:
        initial=append(j)
        assert initial["receipt_seq"]==1
    _killed_child(tmp_path,"append")
    with opened(tmp_path) as j:
        assert j.audit()["receipt_count"]==1
        assert append(j)["status"]=="DUPLICATE_IDENTICAL"
        assert j.audit()["receipt_count"]==1
        fresh=append(j,key="AFTER_RECOVERY")
        assert fresh["status"]=="NEW_LOCAL_RECEIPT"
        assert j.audit()["receipt_count"]==2


def test_partial_schema_init_is_atomic_across_hard_process_exit(tmp_path):
    _killed_child(tmp_path,"schema")
    with opened(tmp_path) as j:
        assert j.audit()["receipt_count"]==0
        assert j.db.execute("PRAGMA synchronous").fetchone()==(2,)
        assert append(j)["status"]=="NEW_LOCAL_RECEIPT"
        assert j.audit()["receipt_count"]==1


def test_committed_original_receipt_and_immutable_hash_survive_reopen(tmp_path):
    with opened(tmp_path) as j:
        original=append(j)
        assert j.db.execute("PRAGMA synchronous").fetchone()==(2,)
        sha=j.db.execute(
            "SELECT receipt_sha256 FROM m1_local_receipts WHERE seq=1"
        ).fetchone()[0]
    with opened(tmp_path) as reopened:
        assert reopened.audit()["receipt_count"]==1
        assert reopened.db.execute(
            "SELECT receipt_sha256 FROM m1_local_receipts WHERE seq=1"
        ).fetchone()[0]==sha
        repeat=append(reopened)
        assert repeat["status"]=="DUPLICATE_IDENTICAL"
        assert repeat["first_local_received_utc"]==original["first_local_received_utc"]


def test_two_connections_simultaneous_same_original_are_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    with opened(tmp_path):
        pass
    barrier=Barrier(2)
    def worker():
        with opened(tmp_path) as j:
            barrier.wait(timeout=15)
            return append(j)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(worker) for _ in range(2)]
        result=[f.result(timeout=20) for f in futures]
    assert {x["status"] for x in result}=={"NEW_LOCAL_RECEIPT","DUPLICATE_IDENTICAL"}
    assert {x["receipt_seq"] for x in result}=={1}
    with opened(tmp_path) as j:
        assert j.audit()["receipt_count"]==1
        assert j.pages(capture_key="TEST_CAPTURE",instrument=inst())==(make_page(),)


def test_competing_different_original_metadata_same_key_has_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    with opened(tmp_path):
        pass
    barrier=Barrier(2)
    def worker(ctype):
        with opened(tmp_path) as j:
            barrier.wait(timeout=15)
            try:
                return append(j,replace(make_page(),content_type=ctype))["status"]
            except LocalIntakeCollision:
                return "COLLISION"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(worker,ctype) for ctype in ("application/json","text/plain")]
        result=[f.result(timeout=20) for f in futures]
    assert sorted(result)==["COLLISION","NEW_LOCAL_RECEIPT"]
    with opened(tmp_path) as j:
        assert j.audit()["receipt_count"]==1


def test_read_handoff_is_one_snapshot_despite_competing_new_page(tmp_path,monkeypatch):
    from birzha.application import g2_m1_first_intake as module
    with opened(tmp_path) as reader:
        append(reader)
        with opened(tmp_path) as writer:
            # Keep contention bounded; this is a disposable test DB only.
            writer.db.execute("PRAGMA busy_timeout=35")
            called=[]
            original=module.prepare_m15_staging_preflight
            def concurrent_writer(**kwargs):
                called.append(True)
                assert reader.db.in_transaction
                # After Q-087 page-body selection but before the Q-090
                # receipt metadata query, another connection attempts
                # to commit a late additional page to the SAME capture.
                new_page=make_page([],start=15)
                try:
                    append(writer,new_page)
                except sqlite3.OperationalError:
                    # Rollback-journal SQLite blocks its COMMIT while this
                    # reader's snapshot is active; WAL may permit commit.
                    pass
                return original(**kwargs)
            monkeypatch.setattr(module,"prepare_m15_staging_preflight",
                                concurrent_writer)
            result=reader.original_capture_handoff(
                capture_key="TEST_CAPTURE",instrument=inst(),
                candidate=candidate(),session_notice=notice(),
                evaluated_at=AT+timedelta(seconds=5))
            assert called==[True]
            assert len(result["manifest"]["original_receipts"])==1
            assert result["q088"]["source_page_count"]==1
            assert reader.db.in_transaction is False
            assert result["production_authorized"] is False


def test_handoff_snapshot_rolls_back_on_refusal(tmp_path):
    with opened(tmp_path) as j:
        append(j)
        with pytest.raises(LocalIntakeRefused):
            j.original_capture_handoff(
                capture_key="TEST_CAPTURE",instrument=inst(),
                candidate=replace(candidate(),close=999.),
                session_notice=notice(),evaluated_at=AT+timedelta(seconds=5))
        assert j.db.in_transaction is False
        assert append(j,key="AFTER_REFUSAL")["status"]=="NEW_LOCAL_RECEIPT"
