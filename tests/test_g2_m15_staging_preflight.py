"""Q-088 strictly offline M15 original page+schedule content preflight."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import pytest

from birzha.application.g2_m15_staging_preflight import (
    DryRunRefused, SessionNoticeCandidate, prepare_m15_staging_preflight, VERSION,
)
from test_g2_m1_m15_source_replay import make_page, candidate as m15_candidate, inst

AT=datetime(2026,10,9,7,16,12,tzinfo=timezone.utc)

def notice():
    raw=b"<html><body>UNVERIFIED OPERATOR TEST SESSION</body></html>"
    return SessionNoticeCandidate(
        original_notice_bytes=raw,claimed_notice_sha256=sha256(raw).hexdigest(),
        original_notice_url="https://www.moex.com/ru/markets/test-session",
        observed_at_utc="2026-10-09T06:15:00Z",
        session_identifier="TEST-SBER-20261009",
        session_start_utc="2026-10-09T06:45:00Z",
        session_end_utc="2026-10-09T15:50:00Z",
        engine="stock",market="shares",board="TQBR",secid="SBER")

def preflight(**kwargs):
    p=kwargs.pop("pages",(make_page(),))
    c=kwargs.pop("candidate",m15_candidate())
    witness=kwargs.pop("session_notice",notice())
    return prepare_m15_staging_preflight(instrument=kwargs.pop("instrument",inst()),
        pages=p,candidate=c,session_notice=witness,
        clock=kwargs.pop("clock",lambda:AT),**kwargs)

def test_content_packed_identically_and_admission_never_promoted():
    first=preflight()
    second=preflight()
    assert first["commitment_bytes"]==second["commitment_bytes"]
    assert first["commitment_sha256"]==sha256(first["commitment_bytes"]).hexdigest()
    data=json.loads(first["commitment_bytes"])
    assert data["schema"]==VERSION
    assert data["m15_from_original_pages"]["end"]=="2026-10-09T07:14:59Z"
    assert data["notice_candidate"]["session_id_claim"]=="TEST-SBER-20261009"
    assert data["m1_source_pages"][0]["original_bytes_sha256"]==make_page().raw_sha256
    assert data["first_source_receipt_utc_independently_proven"] is None
    assert len(data["unresolved_admission_reasons"])>=4
    assert first["level_journal_admission_authorized"] is False
    assert data["may_append_level_event_journal"] is False
    assert data["may_issue_forecast"] is False
    assert data["may_execute_trade"] is False
    assert data["may_use_production"] is False

@pytest.mark.parametrize("change", [
    "wrong_secid","wrong_board","wrong_market","wrong_engine","tampered_notice",
    "wrong_url","lookalike_domain","notice_future","unknown_notice","session_early",
    "session_late","notice_after_request","notice_inverted",
])
def test_official_session_candidate_mismatch_rejected(change):
    w=notice()
    if change=="wrong_secid":w=replace(w,secid="GAZP")
    elif change=="wrong_board":w=replace(w,board="TQTF")
    elif change=="wrong_market":w=replace(w,market="bonds")
    elif change=="wrong_engine":w=replace(w,engine="futures")
    elif change=="tampered_notice":w=replace(w,original_notice_bytes=b"CHANGED")
    elif change=="wrong_url":w=replace(w,original_notice_url="http://www.moex.com/ru/test")
    elif change=="lookalike_domain":w=replace(w,original_notice_url="https://moex.com.evil.test/notice")
    elif change=="notice_future":w=replace(w,observed_at_utc="2026-10-09T07:17:00Z")
    elif change=="unknown_notice":w=replace(w,observed_at_utc="2026-10-09T07:16:01Z")
    elif change=="session_early":w=replace(w,session_end_utc="2026-10-09T07:10:00Z")
    elif change=="session_late":w=replace(w,session_start_utc="2026-10-09T07:05:00Z")
    elif change=="notice_after_request":w=replace(w,observed_at_utc="2026-10-09T07:16:00.500Z")
    elif change=="notice_inverted":w=replace(w,session_start_utc="2026-10-10T08:00:00Z")
    with pytest.raises(DryRunRefused):preflight(session_notice=w)

@pytest.mark.parametrize("issue",[
    "self_exact","self_documented","available_time","observed_time",
    "partial","tampered_ohlcv",
])
def test_cannot_upgrade_source_provenance_by_assertion(issue):
    c=m15_candidate()
    if issue=="self_exact":c=replace(c,available_at_confidence="EXACT")
    elif issue=="self_documented":c=replace(c,available_at_confidence="DOCUMENTED")
    elif issue=="available_time":c=replace(c,available_at="2026-10-09T07:16:00Z")
    elif issue=="observed_time":c=replace(c,observed_at="2026-10-09T07:16:00Z")
    elif issue=="partial":c=replace(c,completed=False)
    elif issue=="tampered_ohlcv":c=replace(c,close=c.close+1)
    with pytest.raises(DryRunRefused):preflight(candidate=c)

@pytest.mark.parametrize("mode",["stale","future","naive","before_source"])
def test_local_clock_cannot_masquerade_as_original_receipt(mode):
    at=AT
    if mode=="stale":at=at+timedelta(minutes=10)
    elif mode=="future":at=at-timedelta(minutes=1)
    elif mode=="naive":at=at.replace(tzinfo=None)
    elif mode=="before_source":at=datetime(2026,10,9,7,14,50,tzinfo=timezone.utc)
    with pytest.raises(DryRunRefused):preflight(clock=lambda:at)

def test_changed_source_bytes_change_commitment_and_wrong_source_sha_rejected():
    original=preflight()["commitment_sha256"]
    p=make_page()
    modified=p.raw_body.replace(b"101.0",b"101.1",1)
    modified_page=replace(p,raw_body=modified,raw_sha256=sha256(modified).hexdigest())
    result=preflight(pages=(modified_page,),candidate=replace(m15_candidate(),open=101.1))
    assert result["commitment_sha256"]!=original
    with pytest.raises(DryRunRefused):
        preflight(pages=(replace(p,raw_body=modified),))

def test_invalid_calendar_session_claim_fails_without_network_or_journal(tmp_path):
    # A malformed source page never gets to calendar binding, nor writes.
    p=make_page()
    altered=p.raw_body.replace(b"10:14:59",b"10:14:58")
    with pytest.raises(DryRunRefused):
        preflight(pages=(replace(p,raw_body=altered,
                                raw_sha256=sha256(altered).hexdigest()),))
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize("edge,claim",[
    ("open_inside","2026-10-09T07:00:00.000001Z"),
    ("close_before","2026-10-09T07:14:58Z"),
    ("later_session","2026-10-09T07:15:00Z"),
])
def test_m15_cannot_cross_claimed_exchange_session_boundary(edge,claim):
    witness=notice()
    if edge in ("open_inside","later_session"):
        witness=replace(witness,session_start_utc=claim)
    else:
        witness=replace(witness,session_end_utc=claim)
    with pytest.raises(DryRunRefused, match="lacks M15 interval"):
        preflight(session_notice=witness)


def test_exact_claimed_session_boundaries_do_not_promote_calendar_authority():
    # Boundary inclusivity is precise, but the document/URL and calendar
    # have only the operator's claimed semantics, not independent MOEX proof.
    witness=replace(notice(),session_start_utc="2026-10-09T07:00:00Z",
                    session_end_utc="2026-10-09T07:14:59Z")
    result=preflight(session_notice=witness)
    assert result["manifest"]["notice_candidate"]["calendar_independently_authenticated"] is False
    assert result["manifest"]["may_append_level_event_journal"] is False
    assert result["level_journal_admission_authorized"] is False
