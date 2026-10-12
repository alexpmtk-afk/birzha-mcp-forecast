"""Q-091: mock-only in-governor callback, strict failures and no live traffic."""
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import json
import sqlite3
import pytest

from birzha.application.g2_governed_m1_intake import (
    GovernedCaptureRefused,capture_governed_m1_mock_only,
    FixtureISSResponse,MemoryOnlyISSOpener,
)
from birzha.application.g2_m1_first_intake import OriginalM1LocalIntake,LocalIntakeCollision
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.providers.moex_iss import MoexIssClient,MoexIssError
from birzha.upstream.safety import UpstreamRateLimited
from test_g2_m1_m15_source_replay import inst,rows

T0=datetime(2026,10,9,7,16,3,tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.current=T0
    def __call__(self):
        value=self.current
        self.current+=timedelta(seconds=1)
        return value


class Gate:
    scope="process"
    def pace(self,seconds):
        pass


def body():
    return json.dumps({"candles":{
        "columns":["open","close","high","low","value","volume","begin","end"],
        "data":rows()}},separators=(",",":")).encode()


def FakeResponse(status=200,body_bytes=None,ctype="application/json"):
    return FixtureISSResponse(status=status, headers={"Content-Type":ctype},
                              body=body() if body_bytes is None else body_bytes)


def environment(tmp_path, responses=None):
    opener=MemoryOnlyISSOpener(tuple(
        responses if responses is not None else [FakeResponse()]))
    control=ProcessUpstreamControlPlane(
        gate_factory=lambda _:Gate(),sleeper=lambda _:None)
    provider=MoexIssClient(control_plane=control,opener=opener)
    clock=Clock()
    journal=OriginalM1LocalIntake(
        tmp_path/"g2_m1_receipts_sandbox.sqlite3",
        staging_root=tmp_path,clock=clock)
    return provider,journal,clock,opener.urls


def capture(provider,journal,clock,**opts):
    return capture_governed_m1_mock_only(
        provider=provider,intake=journal,instrument=inst(),
        capture_key=opts.pop("capture_key","CAPTURE"),
        source_day=opts.pop("source_day","2026-10-09"),
        clock=clock,explicit_mock_only=opts.pop("explicit_mock_only",True),
        **opts)


def test_governed_original_response_committed_before_return(tmp_path):
    p,j,c,attempts=environment(tmp_path)
    try:
        got=capture(p,j,c)
        assert got["governed_attempts"]==1
        assert got["status"]=="SANDBOX_RESPONSE_CALLBACK_LOCALLY_CAPTURED"
        assert got["receipt"]["first_local_received_utc"]=="2026-10-09T07:16:05Z"
        assert got["callback_response_utc"]=="2026-10-09T07:16:04Z"
        assert len(attempts)==1
        assert attempts[0].startswith("https://iss.moex.com/iss/engines/stock/")
        assert "interval=1" in attempts[0] and "start=0" in attempts[0]
        pages=j.pages(capture_key="CAPTURE",instrument=inst())
        assert pages[0].raw_body==body()
        assert pages[0].raw_sha256==sha256(body()).hexdigest()
        assert pages[0].request_started_utc=="2026-10-09T07:16:03Z"
        assert pages[0].response_received_utc=="2026-10-09T07:16:04Z"
        assert j.audit()["receipt_count"]==1
        assert got["provider_tls_identity_independently_authenticated"] is False
        assert got["real_moex_transport_used"] is False
        assert got["fixture_transport_source_only"] is True
        assert got["custom_opener_independently_audited"] is False
        assert got["level_journal_admission_authorized"] is False
    finally:
        j.close()


def test_governor_retries_503_but_persists_only_successful_response(tmp_path):
    p,j,c,attempts=environment(tmp_path,[FakeResponse(503),FakeResponse()])
    try:
        got=capture(p,j,c)
        assert got["governed_attempts"]==2 and len(attempts)==2
        assert j.audit()["receipt_count"]==1
    finally:
        j.close()


def test_storage_failure_aborts_with_no_extra_network_attempt(tmp_path,monkeypatch):
    p,j,c,attempts=environment(tmp_path)
    def fail(**kwargs):
        raise sqlite3.OperationalError("simulated storage denial")
    monkeypatch.setattr(j,"append",fail)
    try:
        with pytest.raises(sqlite3.OperationalError,match="storage denial"):
            capture(p,j,c)
        assert len(attempts)==1
        assert j.audit()["receipt_count"]==0
    finally:
        j.close()


@pytest.mark.parametrize("kind",[
    "disabled","wrong_day","bad_start","non_json","empty_body",
    "status_404","bad_json","missing_column","short_row",
    "bad_native_ohlc","future_unselected_minute","duplicate_native_minute",
])
def test_invalid_sources_refused_without_intake(tmp_path,kind):
    raw=body()
    opts={}
    responses=None
    if kind=="disabled":opts["explicit_mock_only"]=False
    elif kind=="wrong_day":opts["source_day"]="2026-9-9"
    elif kind=="bad_start":opts["page_start"]=-1
    elif kind=="non_json":responses=[FakeResponse(ctype="text/html")]
    elif kind=="empty_body":responses=[FakeResponse(body_bytes=b"")]
    elif kind=="status_404":responses=[FakeResponse(status=404)]
    elif kind=="bad_json":responses=[FakeResponse(body_bytes=b"NOT JSON")]
    elif kind=="missing_column":
        payload=json.loads(raw);payload["candles"]["columns"].remove("volume")
        responses=[FakeResponse(body_bytes=json.dumps(payload).encode())]
    elif kind=="short_row":
        payload=json.loads(raw);payload["candles"]["data"][0].pop()
        responses=[FakeResponse(body_bytes=json.dumps(payload).encode())]
    elif kind=="bad_native_ohlc":
        payload=json.loads(raw);payload["candles"]["data"][0][2]=0
        responses=[FakeResponse(body_bytes=json.dumps(payload).encode())]
    elif kind=="future_unselected_minute":
        payload=json.loads(raw)
        future=payload["candles"]["data"][-1][:]
        future[6]="2026-10-09 10:18:00"
        future[7]="2026-10-09 10:18:59"
        payload["candles"]["data"].append(future)
        responses=[FakeResponse(body_bytes=json.dumps(payload).encode())]
    elif kind=="duplicate_native_minute":
        payload=json.loads(raw)
        payload["candles"]["data"][8]=payload["candles"]["data"][7][:]
        responses=[FakeResponse(body_bytes=json.dumps(payload).encode())]
    p,j,c,attempts=environment(tmp_path,responses)
    try:
        with pytest.raises((GovernedCaptureRefused,MoexIssError)):
            capture(p,j,c,**opts)
        assert j.audit()["receipt_count"]==0
        if kind in ("disabled","wrong_day","bad_start"):
            assert attempts==[]
    finally:
        j.close()


def test_real_default_provider_rejected_before_any_network(tmp_path):
    p=MoexIssClient()
    journal=OriginalM1LocalIntake(
        tmp_path/"g2_m1_receipts_sandbox.sqlite3",
        staging_root=tmp_path,clock=Clock())
    try:
        with pytest.raises(GovernedCaptureRefused,match="default/live"):
            capture(p,journal,Clock())
        assert journal.audit()["receipt_count"]==0
    finally:
        journal.close()
        if p._http_client is not None:
            p._http_client.close()


def test_corrupt_staging_journal_prevents_new_governed_attempt(tmp_path):
    p,j,c,attempts=environment(tmp_path)
    try:
        capture(p,j,c)
        j.db.execute("DROP TRIGGER m1_local_no_update")
        j.db.execute("UPDATE m1_local_receipts SET local_received_utc='2000-01-01T00:00:00Z'")
        with pytest.raises(LocalIntakeCollision):
            capture(p,j,c,capture_key="OTHER")
        assert len(attempts)==1
    finally:
        j.close()


def test_existing_governor_limits_all_503_attempts(tmp_path):
    p,j,c,attempts=environment(tmp_path,[FakeResponse(503)]*3)
    try:
        with pytest.raises(UpstreamRateLimited):
            capture(p,j,c)
        assert len(attempts)==3
        assert j.audit()["receipt_count"]==0
    finally:
        j.close()


def test_arbitrary_callable_opener_refused_before_iss_request(tmp_path):
    events=[]
    def arbitrary_opener(request,timeout):
        events.append(request.full_url)
        raise AssertionError("unapproved user-defined opener invoked")
    control=ProcessUpstreamControlPlane(
        gate_factory=lambda _:Gate(),sleeper=lambda _:None)
    provider=MoexIssClient(control_plane=control,opener=arbitrary_opener)
    j=OriginalM1LocalIntake(
        tmp_path/"g2_m1_receipts_sandbox.sqlite3",staging_root=tmp_path,
        clock=Clock())
    try:
        with pytest.raises(GovernedCaptureRefused,match="arbitrary"):
            capture(provider,j,Clock())
        assert events==[]
        assert j.audit()["receipt_count"]==0
    finally:
        j.close()


def test_in_memory_response_rows_must_belong_to_requested_exchange_day(tmp_path):
    wrong=json.loads(body())
    for r in wrong["candles"]["data"]:
        r[6]=r[6].replace("2026-10-09","2026-10-08")
        r[7]=r[7].replace("2026-10-09","2026-10-08")
    p,j,c,attempts=environment(
        tmp_path,[FakeResponse(body_bytes=json.dumps(wrong).encode())])
    try:
        with pytest.raises(GovernedCaptureRefused,match="exchange day"):
            capture(p,j,c)
        assert len(attempts)==1
        assert j.audit()["receipt_count"]==0
    finally:
        j.close()
