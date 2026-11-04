"""Crash/concurrency acceptance: real subprocess mutexes, isolated cloud faults."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from copy import deepcopy

import httpx
import pytest
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.storage.google_writer_guard import workbook_guard
from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge
from test_model3_google_backfill import Book, _service

ARGS=dict(timeframe='D1',from_date='2025-01-03',till_date='2025-01-03',max_rows=1,dry_run=False,owner_confirmed=True)
CHILD="""
import sys
from birzha.storage.google_writer_guard import workbook_guard
with workbook_guard(sys.argv[1]):
 print('OWNED',flush=True)
 sys.stdin.readline()
"""

@pytest.mark.parametrize('crash',(False,True))
def test_os_mutex_fences_processes_clients_and_survives_crash(crash):
    key='isolated-'+uuid.uuid4().hex
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    child=subprocess.Popen([sys.executable,'-c',CHILD,key],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    try:
        assert child.stdout.readline().strip()=='OWNED'
        with pytest.raises(MirrorConflict,match='WRITER_BUSY'):
            with workbook_guard(key): pytest.fail('second process entered')
        with workbook_guard(key+'-other'):
            with workbook_guard(key+'-other'): pass  # real reentrancy
        calls=[]
        native=NativeCanonicalSheets(lambda:'mock',transport=httpx.MockTransport(lambda req:calls.append(req) or httpx.Response(200,json={})))
        with pytest.raises(MirrorConflict,match='WRITER_BUSY'):
            native._request('POST','https://sheets.googleapis.com/v4/spreadsheets/'+key+':batchUpdate',json={})
        bridge=GoogleSheetsBridge.__new__(GoogleSheetsBridge)
        bridge._post_impl=lambda *a,**k:calls.append('legacy')
        with pytest.raises(MirrorConflict,match='WRITER_BUSY'):
            bridge._post('sheet_commit',{'spreadsheet_id':key},idempotency_key='isolated')
        assert calls==[]  # no network or legacy mutation escaped
        if crash: child.kill()
        else: child.stdin.write('release\n');child.stdin.flush()
        child.wait(timeout=10)
        with workbook_guard(key): pass  # OS releases dead owner; journal reconciles next
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=10)


def statuses(book): return [e['status'] for e in book.audit]

@pytest.mark.parametrize('phase',('STARTED','PREPARED'))
def test_audit_unavailable_prevents_any_indicator_write(phase):
    b=Book();real=b.append_quality
    def fail(s,event):
        if event['status']==phase:raise RuntimeError('TOKEN-DO-NOT-LOG')
        real(s,event)
    b.append_quality=fail
    with pytest.raises(RuntimeError):_service(b,True).model3_backfill(**ARGS)
    assert b.writes==0
    assert 'TOKEN-DO-NOT-LOG' not in json.dumps(b.audit)
    assert statuses(b)[-1]=='ERROR'


def test_lost_prepared_receipt_response_reconciles_no_write_without_retry():
    b=Book();real=b.append_quality
    def lose(s,event):
        real(s,event)
        if event['status']=='PREPARED':raise RuntimeError('lost receipt response')
    b.append_quality=lose
    with pytest.raises(RuntimeError):_service(b,True).model3_backfill(**ARGS)
    assert b.writes==0
    b.append_quality=real
    with pytest.raises(MirrorConflict,match='SETTLEMENT_REQUIRED'):_service(b,True).model3_backfill(**ARGS)
    assert b.writes==0 and 'RECOVERY_NOT_OBSERVED' in statuses(b) and statuses(b)[-1]=='ERROR'


def test_failure_before_append_is_not_retried_during_recovery():
    b=Book()
    b.model3_append_rows=lambda *a:(_ for _ in ()).throw(RuntimeError('secret external exception'))
    with pytest.raises(MirrorConflict,match='UNKNOWN_RECONCILE'):_service(b,True).model3_backfill(**ARGS)
    assert b.writes==0
    with pytest.raises(MirrorConflict,match='SETTLEMENT_REQUIRED'):_service(b,True).model3_backfill(**ARGS)
    assert 'secret external exception' not in json.dumps(b.audit)


def test_conflicting_recovery_never_rewrites_or_rolls_back_current_rows():
    b=Book();b.fail_after_commit=True;s=_service(b,True)
    with pytest.raises(MirrorConflict):s.model3_backfill(**ARGS)
    b.features[1][-1]='unexpected concurrent edit'
    current=deepcopy(b.features)
    with pytest.raises(MirrorConflict,match='RECOVERY_CONFLICT'):s.model3_backfill(**ARGS)
    assert b.writes==1 and b.features==current
    assert 'RECOVERY_CONFLICT' in statuses(b) and statuses(b)[-1]=='ERROR'


def test_committed_audit_failure_is_recovered_without_duplicate():
    b=Book();real=b.append_quality;before=deepcopy(b.features)
    def fail(s,e):
        if e['status']=='COMMITTED':raise RuntimeError('lost audit')
        real(s,e)
    b.append_quality=fail
    with pytest.raises(RuntimeError):_service(b,True).model3_backfill(**ARGS)
    assert b.writes==1 and b.features[:len(before)]==before
    b.append_quality=real
    assert _service(b,True).model3_backfill(**ARGS)['status']=='RECOVERED_COMMITTED'
    assert b.writes==1
    assert _service(b,True).model3_backfill(**ARGS)['status']=='MODEL3_NO_MISSING_IN_WINDOW'
    assert b.writes==1


def test_status_failure_after_verified_append_is_logged_without_resubmit():
    b=Book()
    b.update_status=lambda *a:(_ for _ in ()).throw(RuntimeError('secret status token'))
    r=_service(b,True).model3_backfill(**ARGS)
    assert r['status']=='READBACK_PASS_STATUS_UNCONFIRMED' and b.writes==1
    assert statuses(b)[-2:]==['COMMITTED','STATUS_UNCONFIRMED']
    assert 'secret status token' not in json.dumps(b.audit)
    assert _service(b,True).model3_backfill(**ARGS)['status']=='MODEL3_NO_MISSING_IN_WINDOW'
    assert b.writes==1


def test_new_instance_repeat_preserves_old_rows_and_complete_receipt_sequence():
    b=Book();before=deepcopy(b.features)
    assert _service(b,True).model3_backfill(**ARGS)['written']
    assert statuses(b)==['STARTED','PREPARED','COMMITTED']
    assert _service(b,True).model3_backfill(**ARGS)['status']=='MODEL3_NO_MISSING_IN_WINDOW'
    assert statuses(b)[-2:]==['STARTED','NO_MISSING']
    assert b.writes==1 and b.features[:len(before)]==before


def test_dry_run_and_denied_owner_never_write_even_audit():
    b=Book()
    _service(b,False).model3_backfill(**{**ARGS,'dry_run':True})
    with pytest.raises(MirrorConflict):_service(b,False).model3_backfill(**ARGS)
    assert b.writes==0 and b.audit==[]


def test_late_google_commit_after_empty_read_is_not_duplicated():
    b=Book();real=b.model3_append_rows;pending=[]
    def late(*args):
        pending.append(args)
        raise RuntimeError('network timeout while Google still processes request')
    b.model3_append_rows=late;s=_service(b,True)
    with pytest.raises(MirrorConflict,match='UNKNOWN_RECONCILE'):s.model3_backfill(**ARGS)
    for _ in range(2):
        with pytest.raises(MirrorConflict,match='SETTLEMENT_REQUIRED'):s.model3_backfill(**ARGS)
    assert len(pending)==1 and b.writes==0
    real(*pending[0])  # original request eventually commits, not a new invocation
    assert s.model3_backfill(**ARGS)['status']=='RECOVERED_COMMITTED'
    assert s.model3_backfill(**ARGS)['status']=='MODEL3_NO_MISSING_IN_WINDOW'
    assert b.writes==1 and len(pending)==1


def test_entire_prepared_to_readback_cycle_excludes_another_service_thread():
    from concurrent.futures import ThreadPoolExecutor
    b=Book();key='isolated-full-cycle-'+uuid.uuid4().hex
    b.writer_guard=lambda symbol:workbook_guard(key)
    real=b.append_quality;attempts=[]
    def hook(symbol,event):
        real(symbol,event)
        if event['status']=='PREPARED':
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(_service(b,True).model3_backfill,**ARGS)
                with pytest.raises(MirrorConflict,match='WRITER_BUSY'):future.result(timeout=5)
            attempts.append('blocked')
    b.append_quality=hook
    assert _service(b,True).model3_backfill(**ARGS)['written']
    assert attempts==['blocked'] and b.writes==1
    assert statuses(b)==['STARTED','PREPARED','COMMITTED']


def test_native_network_loss_never_resubmits_post():
    calls=[]
    def handler(req):
        calls.append(req)
        raise httpx.ReadTimeout('private token must not escape')
    t=NativeCanonicalSheets(lambda:'isolated-secret',transport=httpx.MockTransport(handler))
    with pytest.raises(MirrorConflict,match='no automatic write retry') as exc:
        t._request('POST','https://sheets.googleapis.com/v4/spreadsheets/isolated-timeout:batchUpdate',json={})
    assert len(calls)==1 and 'private token' not in str(exc.value)


def test_source_changes_are_logged_before_any_target_write():
    b=Book();s=_service(b,True);calls=[]
    def read(symbol,tf):
        rows=deepcopy(b.source);calls.append(tf)
        if len(calls)>1:rows[1][rows[0].index('close')]+=1
        return rows
    s._schemas=read
    with pytest.raises(MirrorConflict,match='SOURCE_CHANGED'):s.model3_backfill(**ARGS)
    assert b.writes==0 and statuses(b)==['STARTED','ERROR']


def test_recovery_read_failure_records_new_attempt_and_sanitized_error():
    b=Book();b.fail_after_commit=True;s=_service(b,True)
    with pytest.raises(MirrorConflict):s.model3_backfill(**ARGS)
    b.read_rows=lambda *a:(_ for _ in ()).throw(RuntimeError('private recovery response'))
    with pytest.raises(RuntimeError):s.model3_backfill(**ARGS)
    assert statuses(b)[-2:]==['STARTED','ERROR']
    assert b.writes==1 and 'private recovery response' not in json.dumps(b.audit)
