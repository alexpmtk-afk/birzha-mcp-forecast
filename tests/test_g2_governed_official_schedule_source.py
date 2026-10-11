"""Offline governed HTML/PDF MOEX schedule capture with fake httpx network."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import httpx
import pytest

from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.upstream.capture_scope import (
    CaptureScopeRefused, active_capture_scope, bounded_market_capture,
)
from scripts.g2_capture_official_schedule_pages import (
    ScheduleCaptureRefused, capture_official_schedule_pages,
    prepare_schedule_envelope,
)
from test_g2_official_schedule_witness import witness

FIRST="https://www.moex.com/n94172"
SECOND="https://www.moex.com/n96571?nt=106"


class FakeClock:
    def __init__(self):
        self.value=1000.0
        self.start=datetime(2026,10,10,12,0,tzinfo=timezone.utc)

    def monotonic(self):
        return self.value

    def sleep(self,s):
        self.value+=s

    def utc(self):
        return self.start+timedelta(seconds=self.value-1000)


def settings(fake, callback):
    client=httpx.Client(transport=httpx.MockTransport(callback),follow_redirects=False)
    control=ProcessUpstreamControlPlane(
        clock=fake.monotonic,sleeper=fake.sleep
    )
    return client,control


def test_two_real_raw_original_html_pages_have_exact_safe_receipts_and_hashes():
    clock=FakeClock()
    calls=[]
    def mocked(req):
        calls.append(str(req.url))
        return httpx.Response(
            200,stream=httpx.ByteStream(b"raw html received at exact allowed url "+str(req.url).encode()),
            headers={"Content-Type":"text/html; charset=utf-8",
                     "Date":"Sat, 10 Oct 2026 12:00:00 GMT"}
        )
    client,control=settings(clock,mocked)
    try:
        result=capture_official_schedule_pages(
            (FIRST,SECOND),clock=clock.utc,monotonic=clock.monotonic,
            client=client,control_plane=control,
        )
        assert calls==[FIRST,SECOND]
        assert len(result["source_documents"])==2
        for doc in result["source_documents"]:
            from base64 import b64decode
            body=b64decode(doc["body_b64"])
            assert sha256(body).hexdigest()==doc["sha256"]
            assert doc["url"] in calls
            assert doc["transport_claim"]=="SUPPLIED_CLIENT_TRANSPORT_UNVERIFIED"
            assert doc["http_date_untrusted"] is not None
        assert result["network_attempts_in_market_scope"]==2
        assert result["schedule_notice_completeness_independently_verified"] is False
        assert result["production_authorized"] is False
        assert active_capture_scope() is None
    finally:
        client.close()


@pytest.mark.parametrize("url",[
    "http://www.moex.com/n94172",
    "https://moex.com.evil.invalid/n94172",
    "https://user:pw@moex.com/n94172",
    "https://www.moex.com/n94172#fragment",
    "https://www.moex.com/n94172?access_token=secret",
    "https://www.moex.com/n94172?apikey=secret",
    "https://www.moex.com/n94172\nHost: evil",
])
def test_bad_address_or_secret_refuses_before_any_network(url):
    clock=FakeClock(); called=[]
    client,control=settings(clock,lambda req:called.append(str(req.url)) or httpx.Response(200))
    try:
        with pytest.raises(ScheduleCaptureRefused):
            capture_official_schedule_pages(
                (url,),clock=clock.utc,monotonic=clock.monotonic,
                client=client,control_plane=control,
            )
        assert called==[]
    finally:
        client.close()


@pytest.mark.parametrize("problem",["redirect","nonhtml","empty","oversized","serverfail"])
def test_partial_or_unacceptable_result_never_returned_as_complete_inventory(problem):
    clock=FakeClock(); called=[]
    def handler(req):
        called.append(str(req.url))
        if len(called)==1:
            return httpx.Response(200,stream=httpx.ByteStream(b"<html>first</html>"),
                                  headers={"Content-Type":"text/html"})
        if problem=="redirect":
            return httpx.Response(302,headers={"Location":"https://evil.invalid"})
        if problem=="nonhtml":
            return httpx.Response(200,stream=httpx.ByteStream(b"{}"),
                                  headers={"Content-Type":"application/json"})
        if problem=="empty":
            return httpx.Response(200,stream=httpx.ByteStream(b""),
                                  headers={"Content-Type":"text/html"})
        if problem=="oversized":
            return httpx.Response(200,stream=httpx.ByteStream(b"X"*1_500_001),
                                  headers={"Content-Type":"text/html"})
        return httpx.Response(404)
    client,control=settings(clock,handler)
    try:
        with pytest.raises((ScheduleCaptureRefused,RuntimeError)):
            capture_official_schedule_pages(
                (FIRST,SECOND),clock=clock.utc,monotonic=clock.monotonic,
                client=client,control_plane=control,
            )
        assert called==[FIRST,SECOND]
    finally:
        client.close()


def test_schedule_http_429_retry_after_outside_remaining_time_never_retried():
    clock=FakeClock();called=[]
    def handler(req):
        called.append(str(req.url))
        return httpx.Response(429,headers={"Retry-After":"30"})
    client,control=settings(clock,handler)
    try:
        with bounded_market_capture(
            max_attempts=4,deadline_seconds=5,monotonic=clock.monotonic
        ):
            with pytest.raises(CaptureScopeRefused,match="CAPTURE_DEADLINE_EXCEEDED"):
                capture_official_schedule_pages(
                    (FIRST,),clock=clock.utc,client=client,control_plane=control,
                )
        assert len(called)==1
        assert clock.value==1000.0
    finally:
        client.close()


def test_existing_market_scope_counts_schedule_calls_together_with_candles():
    clock=FakeClock();called=[]
    def handler(req):
        called.append(str(req.url))
        return httpx.Response(200,stream=httpx.ByteStream(b"<html>receipt</html>"),
                              headers={"Content-Type":"text/html"})
    client,control=settings(clock,handler)
    try:
        with bounded_market_capture(max_attempts=1,monotonic=clock.monotonic) as scope:
            one=capture_official_schedule_pages(
                (FIRST,),clock=clock.utc,client=client,control_plane=control,
            )
            assert one["network_attempts_in_market_scope"]==1
            with pytest.raises(CaptureScopeRefused,match="CAPTURE_TOTAL_ATTEMPTS_EXCEEDED"):
                capture_official_schedule_pages(
                    (SECOND,),clock=clock.utc,client=client,control_plane=control,
                )
            assert scope.physical_attempts==1
        assert called==[FIRST]
    finally:
        client.close()


def test_manual_session_extraction_must_bind_to_exact_original_captured_bytes():
    clock=FakeClock()
    manual=witness()
    bodies=[
        b"<html>MOEX schedule original synthetic fixture v1. Do NOT treat as real official page</html>",
        b"<html>MOEX corrected synthetic session schedule fixture v2</html>",
    ]
    client,control=settings(clock,lambda req: httpx.Response(
        200,stream=httpx.ByteStream(bodies[0] if str(req.url)==FIRST else bodies[1]),
        headers={"Content-Type":"text/html"}
    ))
    try:
        capture=capture_official_schedule_pages(
            (FIRST,SECOND),clock=clock.utc,monotonic=clock.monotonic,
            client=client,control_plane=control,
        )
        manual["forecast_id"]="synthetic-forecast"
        manual["revisions"][0]["source_document_sha256"]=sha256(bodies[0]).hexdigest()
        manual["revisions"][1]["source_document_sha256"]=sha256(bodies[1]).hexdigest()
        envelope=prepare_schedule_envelope(
            capture=capture,forecast_id=manual["forecast_id"],
            instrument=manual["instrument"],
            session_identifier=manual["session_identifier"],
            revisions=manual["revisions"],
        )
        decoded=json.loads(envelope)
        assert decoded["source_documents"][0]["sha256"]==sha256(bodies[0]).hexdigest()
        assert "session_close_utc" in decoded["revisions"][0]
        assert "session_close_utc" not in decoded["source_documents"][0]
        capture["source_documents"][0]["sha256"]="0"*64
        with pytest.raises(ScheduleCaptureRefused,match="INVENTORY_CHANGED"):
            prepare_schedule_envelope(
                capture=capture,forecast_id=manual["forecast_id"],
                instrument=manual["instrument"],
                session_identifier=manual["session_identifier"],
                revisions=manual["revisions"],
            )
    finally:
        client.close()


def test_unchanged_source_sha_is_not_proof_notice_completeness():
    clock=FakeClock()
    client,control=settings(clock,lambda req:httpx.Response(
        200,stream=httpx.ByteStream(b"<html>no validation of later notices</html>"),
        headers={"Content-Type":"text/html"},
    ))
    try:
        result=capture_official_schedule_pages(
            (FIRST,),clock=clock.utc,monotonic=clock.monotonic,
            client=client,control_plane=control,
        )
        assert result["schedule_notice_completeness_independently_verified"] is False
        assert result["source_authorship_independently_attested"] is False
        assert result["trading_session_interpretation_certified"] is False
    finally:
        client.close()


@pytest.mark.parametrize("encoding",["gzip","br","deflate","zstd"])
def test_original_http_entity_cannot_be_silently_decompressed(encoding):
    """httpx.iter_bytes decodes content: unsigned stored bytes would change."""
    fake=FakeClock()
    called=[]
    def reply(req):
        called.append(str(req.url))
        return httpx.Response(
            200,stream=httpx.ByteStream(b"not actually compressed; rejection occurs before decoding"),
            headers={"Content-Type":"text/html","Content-Encoding":encoding},
        )
    client,control=settings(fake,reply)
    try:
        with pytest.raises(ScheduleCaptureRefused,match="SCHEDULE_BODY_TOO_LARGE_OR_EMPTY"):
            capture_official_schedule_pages(
                (FIRST,),clock=fake.utc,monotonic=fake.monotonic,
                client=client,control_plane=control,
            )
        assert called==[FIRST]
    finally:
        client.close()
