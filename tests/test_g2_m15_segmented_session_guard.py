"""Q-096: synthetic structured schedule content; no authentic MOEX session."""
from dataclasses import replace
from hashlib import sha256
import json
import pytest

from birzha.application.g2_m15_segmented_session_guard import (
    check_normalized_session_segments, SessionSegmentRefused, SCHEMA,
)
from test_g2_m15_staging_preflight import preflight, notice
from test_g2_m1_m15_source_replay import inst


def witness(segments=None, **changes):
    first = notice()
    values = {
        "schema":SCHEMA,
        "instrument":{"engine":"stock","market":"shares",
                      "board":"TQBR","secid":"SBER"},
        "session_id":first.session_identifier,
        "session_start_utc":first.session_start_utc,
        "session_end_utc":first.session_end_utc,
        "notice_observed_utc":first.observed_at_utc,
        "segments":segments if segments is not None else [
            {"start_utc":"2026-10-09T06:45:00Z",
             "end_utc":"2026-10-09T09:15:00Z",
             "kind":"CONTINUOUS_TRADING"},
            {"start_utc":"2026-10-09T09:15:00Z",
             "end_utc":"2026-10-09T09:30:00Z",
             "kind":"CLOSED_BREAK"},
            {"start_utc":"2026-10-09T09:30:00Z",
             "end_utc":"2026-10-09T15:50:00Z",
             "kind":"CONTINUOUS_TRADING"}],
    }
    values.update(changes)
    raw=json.dumps(values,sort_keys=True,separators=(",",":")).encode()
    return replace(first, original_notice_bytes=raw,
                   claimed_notice_sha256=sha256(raw).hexdigest())


def check(w):
    return check_normalized_session_segments(
        preflight=preflight(session_notice=w),instrument=inst(),notice=w)


def test_complete_claimed_continuous_interval_is_content_only():
    w=witness()
    out=check(w)
    assert out["status"]=="CLAIMED_SEGMENT_CONTENT_PASS_EXTERNAL_CALENDAR_BLOCKED"
    assert out["manifest"]["accepted_continuous_segment_index"]==0
    assert out["manifest"]["market_session_independently_authenticated"] is False
    assert out["manifest"]["source_transport_authenticated"] is False
    assert out["level_journal_admission_authorized"] is False
    assert out["production_authorized"] is False


@pytest.mark.parametrize("kind",[
    "break_middle","gap_middle","auction_only","overlap",
    "outside","unknown_kind","wrong_identity","false_session",
    "unbounded","changed_notice","notice_inversion",
])
def test_gap_overlap_and_untrusted_normalized_notice_are_refused(kind):
    w=witness()
    doc=json.loads(w.original_notice_bytes)
    if kind=="break_middle":
        doc["segments"]=[
            {"start_utc":"2026-10-09T06:45:00Z",
             "end_utc":"2026-10-09T07:05:00Z","kind":"CONTINUOUS_TRADING"},
            {"start_utc":"2026-10-09T07:05:00Z",
             "end_utc":"2026-10-09T07:10:00Z","kind":"CLOSED_BREAK"},
            {"start_utc":"2026-10-09T07:10:00Z",
             "end_utc":"2026-10-09T15:50:00Z","kind":"CONTINUOUS_TRADING"}]
    elif kind=="gap_middle":
        doc["segments"]=[
            {"start_utc":"2026-10-09T06:45:00Z",
             "end_utc":"2026-10-09T07:05:00Z","kind":"CONTINUOUS_TRADING"},
            {"start_utc":"2026-10-09T07:10:00Z",
             "end_utc":"2026-10-09T15:50:00Z","kind":"CONTINUOUS_TRADING"}]
    elif kind=="auction_only":
        doc["segments"][0]["kind"]="AUCTION"
    elif kind=="overlap":
        doc["segments"][1]["start_utc"]="2026-10-09T09:00:00Z"
    elif kind=="outside":
        doc["segments"][0]["start_utc"]="2026-10-09T06:00:00Z"
    elif kind=="unknown_kind":
        doc["segments"][0]["kind"]="UNDEFINED"
    elif kind=="wrong_identity":
        doc["instrument"]["secid"]="GAZP"
    elif kind=="false_session":
        doc["session_id"]="FALSE"
    elif kind=="unbounded":
        doc["segments"]=doc["segments"]*10
    elif kind=="changed_notice":
        w=replace(w,claimed_notice_sha256="0"*64)
        with pytest.raises(SessionSegmentRefused):check(w)
        return
    elif kind=="notice_inversion":
        doc["session_end_utc"]="2026-10-09T06:40:00Z"
    raw=json.dumps(doc,sort_keys=True,separators=(",",":")).encode()
    w=replace(w,original_notice_bytes=raw,claimed_notice_sha256=sha256(raw).hexdigest())
    with pytest.raises(SessionSegmentRefused):
        check(w)


def test_q088_manifest_swap_rejected_even_if_normalized_json_matches():
    a=witness()
    b=witness(segments=[
        {"start_utc":"2026-10-09T06:45:00Z",
         "end_utc":"2026-10-09T15:50:00Z",
         "kind":"CONTINUOUS_TRADING"}])
    original=preflight(session_notice=a)
    with pytest.raises(SessionSegmentRefused):
        check_normalized_session_segments(
            preflight=original,instrument=inst(),notice=b)


def test_doc_url_and_hash_cannot_certify_official_moex_sessions():
    result=check(witness())
    assert result["manifest"]["market_session_independently_authenticated"] is False
    assert result["manifest"]["independent_original_network_receipt"] is False
