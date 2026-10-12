"""Sandbox Q-091: governed M1 HTTP response to original local intake.

Only a MoexIssClient with a custom fake opener may enter. Never runs the
default live MOEX httpx transport or changes production provider paths.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Callable
from urllib.parse import urlencode

from birzha.application.g2_m1_first_intake import OriginalM1LocalIntake, _key
from birzha.application.g2_m1_m15_source_replay import (
    OriginalM1Page, SourceReplayRefused, _identity, _strict_json, _table,
    COLUMNS, PAGE_SIZE, MAX_BYTES, _minute,
)
from birzha.application.upstream_control import UpstreamRequestGovernor
from birzha.domain.market import Instrument
from birzha.providers.moex_iss import IssResponse, MoexIssClient, MoexIssError, ISS_BASE

VERSION = "G2_GOVERNED_M1_CALLBACK_SANDBOX_V1"


class GovernedCaptureRefused(ValueError):
    """Sandbox source response not admissible."""


def _stamp(value: datetime) -> str:
    if (not isinstance(value, datetime) or value.tzinfo is None
        or value.utcoffset() is None):
        raise GovernedCaptureRefused("offset-aware callback clock required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00","Z")


def _date(value: str) -> str:
    if not isinstance(value,str):
        raise GovernedCaptureRefused("exact source date required")
    try:
        parsed=date.fromisoformat(value)
    except ValueError as exc:
        raise GovernedCaptureRefused("invalid source day") from exc
    if parsed.isoformat()!=value:
        raise GovernedCaptureRefused("noncanonical source day")
    return value


def capture_governed_m1_mock_only(
    *, provider: MoexIssClient, intake: OriginalM1LocalIntake,
    instrument: Instrument, capture_key: str, source_day: str,
    page_start: int = 0,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    explicit_mock_only: bool = False,
) -> dict:
    """Capture one response immediately in an existing governed attempt.

    No network enablement is supplied. An external custom opener can make
    untrustworthy claims: this function only tests callback/plumbing.
    """
    if explicit_mock_only is not True:
        raise GovernedCaptureRefused("explicit mock-only opt-in required")
    if (type(provider) is not MoexIssClient
        or not isinstance(provider._governor, UpstreamRequestGovernor)
        or provider._http_client is not None):
        raise GovernedCaptureRefused("default/live MOEX transport forbidden")
    if not isinstance(intake, OriginalM1LocalIntake):
        raise GovernedCaptureRefused("isolated original source intake required")
    if type(page_start) is not int or not 0 <= page_start < 12*PAGE_SIZE:
        raise GovernedCaptureRefused("page_start outside bounded range")
    try:
        exact = _identity(instrument)
        key = _key(capture_key)
    except (TypeError,ValueError) as exc:
        raise GovernedCaptureRefused("invalid exact instrument or key") from exc
    day=_date(source_day)
    intake.audit()  # Corrupt sink must fail before any HTTP attempt.
    engine,market,board,secid=exact.split("/")
    path=(f"/engines/{engine}/markets/{market}/boards/{board}/"
          f"securities/{secid}/candles.json")
    url=f"{ISS_BASE}{path}?"+urlencode(
        {"interval":1,"start":page_start,"from":day,"till":day})
    attempts=0
    capture=None

    def original_response_attempt() -> IssResponse:
        nonlocal attempts,capture
        attempts+=1
        started=_stamp(clock())
        # NEVER call this outside the existing governor.execute().
        response=provider._one_attempt(url)
        received=_stamp(clock())
        if received<started:
            raise GovernedCaptureRefused("backwards request clock")
        if (datetime.fromisoformat(received.replace("Z","+00:00")) -
            datetime.fromisoformat(started.replace("Z","+00:00"))).total_seconds()>90:
            raise GovernedCaptureRefused("unbounded request duration")
        if not isinstance(response,IssResponse):
            raise GovernedCaptureRefused("untyped provider response")
        if response.status_code!=200:
            return response  # governor owns 429/5xx retry.
        if response.transport_failure or not isinstance(response.headers,dict):
            raise GovernedCaptureRefused("transport failure or absent headers")
        content_type=next(
            (str(v) for k,v in response.headers.items()
             if str(k).lower()=="content-type"),"")
        if content_type.split(";")[0].strip().lower()!="application/json":
            raise GovernedCaptureRefused("non-JSON HTTP response")
        raw=response.body
        if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_BYTES:
            raise GovernedCaptureRefused("missing or oversized source bytes")
        try:
            decoded=_strict_json(raw)
            columns,rows=_table(decoded,"candles")
            if (not set(COLUMNS).issubset(columns) or len(rows)>PAGE_SIZE
                or any(not isinstance(r,list) or len(r)!=len(columns) for r in rows)):
                raise GovernedCaptureRefused("malformed original M1 page")
            # Validate native contents at callback, before reporting a
            # successful immutable source receipt. This is still NOT MOEX
            # provenance certification.
            observed = datetime.fromisoformat(received.replace("Z","+00:00"))
            last_begin = None
            for row in rows:
                minute = _minute(dict(zip(columns,row)))
                if minute["end"] > observed:
                    raise GovernedCaptureRefused("uncompleted native M1 source row")
                if last_begin is not None and minute["begin"] <= last_begin:
                    raise GovernedCaptureRefused("duplicate or reordered native M1")
                last_begin = minute["begin"]
        except SourceReplayRefused as exc:
            raise GovernedCaptureRefused("invalid original M1 JSON source") from exc
        page=OriginalM1Page(
            raw_body=raw,raw_sha256=sha256(raw).hexdigest(),
            request_url=url,http_status=200,content_type=content_type,
            request_started_utc=started,response_received_utc=received,
            start=page_start)
        # Original response callback: persist BEFORE returning to governor.
        # SQLite failures propagate, never silently trigger new HTTP retries.
        receipt=intake.append(capture_key=key,instrument=instrument,page=page)
        capture={"receipt":receipt,"original_raw_sha256":page.raw_sha256,
                 "request_url_claim":url,"callback_response_utc":received}
        return response

    response=provider._governor.execute([url],lambda _: original_response_attempt)[0]
    if response.status_code!=200 or capture is None:
        raise MoexIssError("mock governed M1 response had no accepted original bytes")
    return {
        "schema":VERSION,"status":"SANDBOX_RESPONSE_CALLBACK_LOCALLY_CAPTURED",
        "capture_key":key,"instrument":exact,"page_start":page_start,
        "governed_attempts":attempts,**capture,
        # A custom opener could perform network I/O; the sandbox cannot
        # independently prove that it was a fake despite test-only usage.
        "real_moex_transport_used":None,
        "custom_opener_independently_audited":False,
        "provider_tls_identity_independently_authenticated":False,
        "original_network_arrival_independently_proven":False,
        "local_clock_independently_attested":False,
        "exchange_calendar_independently_verified":False,
        "level_journal_admission_authorized":False,
        "forecast_or_trade_authorized":False,
        "production_authorized":False,
    }
