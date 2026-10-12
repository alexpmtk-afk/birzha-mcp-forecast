"""Q-088: offline M1->M15 receipt and schedule-candidate binding preflight.

No network, disk writes, LevelEventPilot.append or independent authentication.
A successful content match is NOT prospective source admission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Callable
from urllib.parse import urlsplit

from birzha.application.g2_m1_m15_source_replay import (
    OriginalM1Page, SourceReplayRefused, replay_completed_m15,
)
from birzha.domain.market import Candle, Instrument

VERSION = "G2_M15_SESSION_RECEIPT_DRYRUN_V1"
MAX_SCHEDULE_BYTES = 1_500_000
MAX_RECEIPT_AGE_SECONDS = 300
OPEN_FLAGS = (
    "SOURCE_TRANSPORT_NOT_INDEPENDENTLY_AUTHENTICATED",
    "SOURCE_FIRST_RECEIPT_NOT_INDEPENDENTLY_ATTESTED",
    "EXCHANGE_SESSION_CALENDAR_NOT_INDEPENDENTLY_VERIFIED",
    "SOURCE_BYTES_TO_CANDLE_NOT_CAPTURED_AT_ORIGINAL_FIRST_RECEIPT",
    "LIVE_JOURNAL_INTEGRATION_NOT_AUTHORIZED",
)


class DryRunRefused(ValueError):
    """A requested research preflight cannot be bound to its source evidence."""


@dataclass(frozen=True, slots=True)
class SessionNoticeCandidate:
    """Operator-supplied claimed official notice, NOT certified MOEX schedule."""
    original_notice_bytes: bytes
    claimed_notice_sha256: str
    original_notice_url: str
    observed_at_utc: str
    session_identifier: str
    session_start_utc: str
    session_end_utc: str
    engine: str
    market: str
    board: str
    secid: str


def _time(value: str) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError("not a timestamp")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing timezone offset")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise DryRunRefused("valid offset-aware session and receipt times required") from exc


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def _notice(candidate: SessionNoticeCandidate, instrument: Instrument, *,
            start: datetime, end: datetime,
            first_request_at: datetime, evaluated: datetime) -> dict:
    if not isinstance(candidate, SessionNoticeCandidate):
        raise TypeError("typed session notice candidate required")
    if (not isinstance(candidate.original_notice_bytes, bytes)
        or not 0 < len(candidate.original_notice_bytes) <= MAX_SCHEDULE_BYTES
        or candidate.claimed_notice_sha256 != _sha(candidate.original_notice_bytes)):
        raise DryRunRefused("missing, oversized or modified original notice bytes")
    url = urlsplit(candidate.original_notice_url)
    if (url.scheme != "https" or url.hostname not in {"moex.com", "www.moex.com"}
        or url.port is not None or url.username is not None or
        url.password is not None or url.fragment or not url.path.startswith("/")
        or any(c in candidate.original_notice_url for c in "\n\r\t\\")):
        raise DryRunRefused("notice source must claim exact official HTTPS domain")
    expected = (instrument.engine, instrument.market, instrument.board, instrument.secid)
    actual = (candidate.engine, candidate.market, candidate.board, candidate.secid)
    if actual != expected or not candidate.session_identifier or not isinstance(candidate.session_identifier,str):
        raise DryRunRefused("session witness and original market identity differ")
    notice_observed = _time(candidate.observed_at_utc)
    session_start = _time(candidate.session_start_utc)
    session_end = _time(candidate.session_end_utc)
    if (not session_start < session_end or
        session_end-session_start > timedelta(hours=24) or
        not session_start <= start < end <= session_end or
        notice_observed > first_request_at or notice_observed > evaluated):
        raise DryRunRefused("session candidate not known before request or lacks M15 interval")
    return {
        "session_id_claim": candidate.session_identifier,
        "session_start_utc_claim": _stamp(session_start),
        "session_end_utc_claim": _stamp(session_end),
        "notice_observed_utc_claim": _stamp(notice_observed),
        "notice_original_url_claim": candidate.original_notice_url,
        "notice_original_bytes_sha256": candidate.claimed_notice_sha256,
        "calendar_independently_authenticated": False,
    }


def prepare_m15_staging_preflight(
    *, instrument: Instrument,
    pages: tuple[OriginalM1Page, ...],
    candidate: Candle,
    session_notice: SessionNoticeCandidate,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict:
    """Bind actual supplied M1 bytes and session candidate; grant NO authority.

    The returned commitment is suitable for *future* independent timestamp
    anchoring, but this function NEVER records an original first receipt.
    """
    if not isinstance(candidate, Candle):
        raise TypeError("original candidate Candle required")
    if (candidate.completed is not True or
        candidate.available_at_confidence != "UNKNOWN" or
        candidate.available_at is not None or candidate.observed_at is not None):
        raise DryRunRefused("caller cannot self-assert EXACT or receipt provenance")
    when = clock()
    if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
        raise DryRunRefused("preflight clock must be offset-aware")
    evaluated = when.astimezone(timezone.utc)
    if not isinstance(pages,tuple) or not pages:
        raise DryRunRefused("nonempty original M1 page tuple required")
    try:
        replay = replay_completed_m15(
            instrument=instrument, pages=pages, candidate=candidate,
            m15_begin_utc=candidate.begin, evaluated_at_utc=_stamp(evaluated))
    except SourceReplayRefused as exc:
        raise DryRunRefused("original M1 source preflight refused") from exc
    observed = _time(pages[-1].response_received_utc)
    first_request = _time(pages[0].request_started_utc)
    if not observed <= evaluated or (evaluated-observed).total_seconds() > MAX_RECEIPT_AGE_SECONDS:
        raise DryRunRefused("source receipt claim stale at local preflight clock")
    candle = replay["m15_from_source"]
    notice = _notice(session_notice,instrument,
                     start=_time(candle["begin"]),end=_time(candle["end"]),
                     first_request_at=first_request,evaluated=evaluated)
    source = [{
        "ordinal": i, "page_offset": p.start,
        "request_url_claim": p.request_url,
        "original_bytes_sha256": _sha(p.raw_body),
        "request_started_utc_claim": _stamp(_time(p.request_started_utc)),
        "response_received_utc_claim": _stamp(_time(p.response_received_utc)),
    } for i,p in enumerate(pages)]
    manifest = {
        "schema": VERSION,
        "instrument": replay["instrument"],
        "m15_from_original_pages": candle,
        "m1_source_pages": source,
        "m1_replay_schema": replay["schema"],
        "notice_candidate": notice,
        "preflight_evaluated_at_local_clock_utc": _stamp(evaluated),
        "first_source_receipt_utc_independently_proven": None,
        "external_tsa_verification_status": "NOT_PERFORMED",
        "source_provider_authentication_status": "NOT_PERFORMED",
        "official_session_verification_status": "NOT_PERFORMED",
        "unresolved_admission_reasons": list(OPEN_FLAGS),
        "may_append_level_event_journal": False,
        "may_issue_forecast": False,
        "may_execute_trade": False,
        "may_use_production": False,
    }
    original = _json(manifest)
    return {
        "schema": VERSION,
        "status": "OFFLINE_CONTENT_PRECHECK_PASS_EXTERNAL_ADMISSION_BLOCKED",
        "manifest": manifest,
        "commitment_bytes": original,
        "commitment_sha256": _sha(original),
        "commitment_requires_independent_attestation": True,
        "source_page_count":len(pages),
        "level_journal_admission_authorized": False,
        "production_authorized": False,
    }
