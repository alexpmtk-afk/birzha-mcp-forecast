"""Q-096: strict offline gap/break veto for NORMALIZED session claims.

The original normalized document bytes are operator supplied. This checks
internal consistency of claimed intervals, NOT official MOEX authenticity.
"""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json

from birzha.application.g2_m15_staging_preflight import (
    SessionNoticeCandidate, DryRunRefused, _time, _json, _sha,
    VERSION as PREFLIGHT_SCHEMA,
)
from birzha.application.g2_m1_m15_source_replay import _strict_json
from birzha.domain.market import Instrument

SCHEMA = "G2_NORMALIZED_SESSION_SEGMENTS_V1"
VERSION = "G2_M15_SESSION_SEGMENT_GUARD_V1"
MAX_SEGMENTS = 24


class SessionSegmentRefused(ValueError):
    """Candidate session intervals cannot support this completed M15."""


def check_normalized_session_segments(
    *, preflight: dict, instrument: Instrument,
    notice: SessionNoticeCandidate,
) -> dict:
    """Fail on fake source bindings, gaps, overlap and boundary crossings.

    A PASS is content-only, and never upgrades a calendar to independent
    official verification or produces a Forecast/LevelEvent admission.
    """
    if not isinstance(preflight, dict) or not isinstance(notice, SessionNoticeCandidate):
        raise SessionSegmentRefused("typed original Q-088 evidence required")
    try:
        manifest = preflight["manifest"]
        raw = preflight["commitment_bytes"]
        if (type(raw) is not bytes or _sha(raw) != preflight["commitment_sha256"]
            or raw != _json(manifest)
            or preflight["schema"] != PREFLIGHT_SCHEMA
            or manifest["schema"] != PREFLIGHT_SCHEMA
            or preflight["status"] !=
               "OFFLINE_CONTENT_PRECHECK_PASS_EXTERNAL_ADMISSION_BLOCKED"
            or preflight["level_journal_admission_authorized"] is not False
            or preflight["production_authorized"] is not False):
            raise SessionSegmentRefused("invalid original Q-088 no-go commitment")
        if (_sha(notice.original_notice_bytes) != notice.claimed_notice_sha256
            or manifest["notice_candidate"]["notice_original_bytes_sha256"] !=
               notice.claimed_notice_sha256
            or manifest["notice_candidate"]["session_id_claim"] != notice.session_identifier
            or manifest["notice_candidate"]["session_start_utc_claim"] !=
               _time(notice.session_start_utc).isoformat().replace("+00:00","Z")
            or manifest["notice_candidate"]["session_end_utc_claim"] !=
               _time(notice.session_end_utc).isoformat().replace("+00:00","Z")
            or manifest["notice_candidate"]["notice_observed_utc_claim"] !=
               _time(notice.observed_at_utc).isoformat().replace("+00:00","Z")):
            raise SessionSegmentRefused("session claim differs from committed source")
        expected = {"engine":instrument.engine,"market":instrument.market,
                    "board":instrument.board,"secid":instrument.secid}
        if manifest["instrument"] != expected:
            raise SessionSegmentRefused("M15 instrument differs from notice")
        doc = _strict_json(notice.original_notice_bytes)
        if set(doc) != {
            "schema","instrument","session_id","session_start_utc",
            "session_end_utc","notice_observed_utc","segments"
        } or doc["schema"] != SCHEMA or doc["instrument"] != expected:
            raise SessionSegmentRefused("unsupported or mismatched normalized notice")
        if (doc["session_id"] != notice.session_identifier
            or _time(doc["session_start_utc"]) != _time(notice.session_start_utc)
            or _time(doc["session_end_utc"]) != _time(notice.session_end_utc)
            or _time(doc["notice_observed_utc"]) != _time(notice.observed_at_utc)):
            raise SessionSegmentRefused("original notice bytes contradict claimed session")
        beginning = _time(manifest["m15_from_original_pages"]["begin"])
        ending = _time(manifest["m15_from_original_pages"]["end"])
        session_start = _time(notice.session_start_utc)
        session_end = _time(notice.session_end_utc)
        if not session_start <= beginning < ending <= session_end:
            raise SessionSegmentRefused("M15 crosses claimed outer session")
        parts = doc["segments"]
        if type(parts) is not list or not 1 <= len(parts) <= MAX_SEGMENTS:
            raise SessionSegmentRefused("missing or unbounded intraday segments")
        last = None
        accepted = None
        for i, segment in enumerate(parts):
            if type(segment) is not dict or set(segment) != {"start_utc","end_utc","kind"}:
                raise SessionSegmentRefused("invalid normalized segment fields")
            a = _time(segment["start_utc"])
            b = _time(segment["end_utc"])
            if (a < session_start or b > session_end or not a < b
                or b - a > timedelta(hours=24)
                or (last is not None and a < last)):
                raise SessionSegmentRefused("overlap, inversion, or out-of-session segment")
            if segment["kind"] not in ("CONTINUOUS_TRADING","AUCTION","CLOSED_BREAK"):
                raise SessionSegmentRefused("unknown session activity kind")
            if a <= beginning and ending <= b and segment["kind"] == "CONTINUOUS_TRADING":
                accepted = i
            last = b
        if accepted is None:
            raise SessionSegmentRefused("M15 not wholly inside one continuous session")
        payload = {
            "schema":VERSION,
            "original_q088_sha256":preflight["commitment_sha256"],
            "original_normalized_notice_sha256":notice.claimed_notice_sha256,
            "exact_instrument":expected,
            "session_id_claim":notice.session_identifier,
            "accepted_continuous_segment_index":accepted,
            "market_session_independently_authenticated":False,
            "source_transport_authenticated":False,
            "independent_original_network_receipt":False,
            "level_journal_admission_authorized":False,
            "forecast_or_trade_authorized":False,
            "production_authorized":False,
        }
        return {
            "status":"CLAIMED_SEGMENT_CONTENT_PASS_EXTERNAL_CALENDAR_BLOCKED",
            "schema":VERSION,
            "manifest":payload,
            "commitment_sha256":_sha(_json(payload)),
            "level_journal_admission_authorized":False,
            "production_authorized":False,
        }
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        raise SessionSegmentRefused("segmented session content verification failed") from exc
