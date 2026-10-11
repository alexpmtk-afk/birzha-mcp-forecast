"""Opt-in bounded official MOEX schedule-page capture; NO automatic network call.

This deliberately collects exact original page bytes BEFORE creating the later
Forecast. It does not parse session times, prove that all notices were found,
certify provider identity, or authorize production. No schedule revisions are
fabricated from fetched HTML/PDF.
"""
from __future__ import annotations

import base64
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re

import httpx

from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.upstream.capture_scope import active_capture_scope, bounded_market_capture
from birzha.upstream.moex import MOEX_ISS_PUBLIC_POLICY
from birzha.application.g2_official_schedule_witness import (
    ALLOWED_CONTENT_TYPES, MAX_DOCS, MAX_RAW_DOCUMENT, _url, canonical,
)

MAX_CALLS = 36
MAX_DURATION_SECONDS = 60.0
MAX_URL_LENGTH = 512
SECRET_FIELD = re.compile(
    r"(?i)(?:^|[?&])(token|api_key|apikey|secret|password|auth|"
    r"access_token|signature|sessionid)="
)


class ScheduleCaptureRefused(ValueError):
    """Safe, bounded error: never expose source URL, payload or TLS internals."""


@dataclass(frozen=True)
class _PageResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    started: str
    ended: str
    truncated: bool = False


def _now(clock) -> str:
    when=clock()
    if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
        raise ScheduleCaptureRefused("SCHEDULE_CLOCK_INVALID")
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_urls(urls: tuple[str, ...]):
    if not isinstance(urls, tuple) or not 1 <= len(urls) <= MAX_DOCS:
        raise ScheduleCaptureRefused("SCHEDULE_URL_INVENTORY_INVALID")
    if len(set(urls)) != len(urls):
        raise ScheduleCaptureRefused("SCHEDULE_URL_INVENTORY_DUPLICATE")
    for url in urls:
        if not isinstance(url, str) or len(url) > MAX_URL_LENGTH or SECRET_FIELD.search(url):
            raise ScheduleCaptureRefused("SCHEDULE_URL_NOT_ALLOWED")
        try:
            _url(url)
        except (ValueError, TypeError) as exc:
            raise ScheduleCaptureRefused("SCHEDULE_URL_NOT_ALLOWED") from exc


def _get_original_page(client: httpx.Client, url: str, clock) -> _PageResponse:
    started=_now(clock)
    try:
        with client.stream("GET", url, follow_redirects=False) as rsp:
            status=rsp.status_code
            headers={
                str(k).lower():str(v) for k,v in rsp.headers.items()
                if str(k).lower() in {"content-type","content-encoding","date","retry-after","location"}
            }
            # Do not load an unbounded HTML document even when Content-Length is absent.
            if status != 200:
                return _PageResponse(status,headers,b"",started,_now(clock))
            # We assert byte identity with the captured HTTP entity. iter_bytes()
            # silently decompresses gzip/br/deflate and loses those originals.
            # Require identity encoding and read raw streamed bytes instead.
            if headers.get("content-encoding", "identity").strip().lower() != "identity":
                return _PageResponse(status,headers,b"",started,_now(clock),True)
            content=bytearray()
            for chunk in rsp.iter_raw():
                if len(content) + len(chunk) > MAX_RAW_DOCUMENT:
                    return _PageResponse(status,headers,b"",started,_now(clock),True)
                content.extend(chunk)
            return _PageResponse(status,headers,bytes(content),started,_now(clock))
    except (httpx.TimeoutException,httpx.TransportError,ConnectionError):
        return _PageResponse(503,{},b"",started,_now(clock))


def capture_official_schedule_pages(
    urls: tuple[str, ...], *, clock=lambda:datetime.now(timezone.utc),
    control_plane: ProcessUpstreamControlPlane | None = None,
    client: httpx.Client | None = None,
    monotonic=None,
) -> dict:
    """Return original bounded responses; whole inventory or no result.

    Uses the SAME active market CaptureScope if invoked before Forecast T0.
    Otherwise a new standalone explicit <=36 attempts/60s scope is used.
    No requests are started unless this function is called explicitly.
    """
    _validate_urls(urls)
    scope=active_capture_scope()
    import time
    context=(nullcontext(scope) if scope is not None else bounded_market_capture(
        max_attempts=MAX_CALLS,deadline_seconds=MAX_DURATION_SECONDS,
        monotonic=monotonic or time.monotonic,
    ))
    own=client is None
    if own:
        client=httpx.Client(
            verify=True,trust_env=False,follow_redirects=False,
            timeout=httpx.Timeout(10.0), headers={
                "Accept":"text/html,application/pdf,text/plain",
                "Accept-Encoding":"identity",
                "User-Agent":"BIRZHA-G2-SCHEDULE-RECEIPT/1.0",
            },
        )
    try:
        controller=control_plane or ProcessUpstreamControlPlane(
            clock=monotonic or time.monotonic
        )
        governor=controller.governor(
            "moex-public-official-schedule", MOEX_ISS_PUBLIC_POLICY
        )
        pages=[]
        with context as current_scope:
            for url in urls:
                current_scope.check()
                result=governor.execute(
                    [url],lambda _:lambda: _get_original_page(client,url,clock)
                )[0]
                current_scope.check()
                if result.status_code != 200:
                    raise ScheduleCaptureRefused("SCHEDULE_HTTP_NON_200")
                if result.truncated or not result.body:
                    raise ScheduleCaptureRefused("SCHEDULE_BODY_TOO_LARGE_OR_EMPTY")
                content_type=result.headers.get("content-type","").split(";",1)[0].strip().lower()
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise ScheduleCaptureRefused("SCHEDULE_MIME_UNSUPPORTED")
                if "location" in result.headers:
                    raise ScheduleCaptureRefused("SCHEDULE_REDIRECT_NOT_ALLOWED")
                pages.append({
                    "url":url,
                    "content_type":content_type,
                    "observed_at_utc":result.ended,
                    "observed_start_utc":result.started,
                    "sha256":sha256(result.body).hexdigest(),
                    "body_b64":base64.b64encode(result.body).decode("ascii"),
                    "http_date_untrusted":result.headers.get("date"),
                    "transport_claim":"HTTPS_CLIENT_WITH_CERT_VALIDATION" if own
                        else "SUPPLIED_CLIENT_TRANSPORT_UNVERIFIED",
                })
            current_scope.check()
            return {
                "schema":"G2_BOUNDED_OFFICIAL_SCHEDULE_RECEIPT_V1",
                "source_documents":pages,
                "source_inventory_sha256":sha256(canonical(pages)).hexdigest(),
                "network_attempts_in_market_scope":current_scope.physical_attempts,
                "schedule_notice_completeness_independently_verified":False,
                "source_authorship_independently_attested":False,
                "trading_session_interpretation_certified":False,
                "production_authorized":False,
            }
    finally:
        if own:
            client.close()


def prepare_schedule_envelope(*, capture: dict, forecast_id: str,
                              instrument: dict, session_identifier: str,
                              revisions: list[dict]) -> bytes:
    """Attach manually reviewed revisions to EXACT captured original bytes.

    Never infer published date, SECID eligibility, session hours or changed
    notices from a document just because its source URL resembles MOEX.
    """
    if (not isinstance(capture,dict)
        or capture.get("schema")!="G2_BOUNDED_OFFICIAL_SCHEDULE_RECEIPT_V1"
        or capture.get("schedule_notice_completeness_independently_verified") is not False
        or capture.get("production_authorized") is not False):
        raise ScheduleCaptureRefused("SCHEDULE_CAPTURE_PROVENANCE_INVALID")
    documents=capture.get("source_documents")
    if (not isinstance(documents,list) or not documents
        or capture.get("source_inventory_sha256")!=sha256(canonical(documents)).hexdigest()):
        raise ScheduleCaptureRefused("SCHEDULE_CAPTURE_INVENTORY_CHANGED")
    # Only the fields in the original #196 envelope are carried forward.
    originals=[{k:item[k] for k in (
        "url","content_type","observed_at_utc","sha256","body_b64",
    )} for item in documents]
    if not isinstance(revisions,list) or not revisions:
        raise ScheduleCaptureRefused("SCHEDULE_NOTICE_EXTRACTION_REQUIRED")
    return canonical({
        "schema":"G2_OFFICIAL_MOEX_SCHEDULE_EVIDENCE_CHAIN_V1",
        "forecast_id":forecast_id,
        "session_identifier":session_identifier,
        "instrument":instrument,
        "source_documents":originals,
        "revisions":revisions,
    })
