"""Versioned official-MOEX-page schedule candidate, source-byte-retained and fail-closed.

This validates provenance CLAIMS and immutable records, not cryptographic MOEX
authenticity. Observed HTTP snapshots and extracted session fields are operator
supplied; no weekday interpolation or permission to trade/release is inferred.
"""
from __future__ import annotations

import base64
import binascii
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from birzha.application.g2_tsa_policy_cutoff_review import CUTOFF_SCHEMA
from birzha.application.g2_time_anchor import ExternalProofRefused

SCHEMA = "G2_OFFICIAL_MOEX_SCHEDULE_EVIDENCE_CHAIN_V1"
MAX_DOCS = 12
MAX_REVISIONS = 24
MAX_RAW_DOCUMENT = 1_500_000
MAX_ENVELOPE = 12_000_000
MOEX_MSK = ZoneInfo("Europe/Moscow")
ALLOWED_CONTENT_TYPES = {"text/html", "application/pdf", "text/plain"}
SESSION_KINDS = {
    "REGULAR", "WEEKEND_ADDITIONAL", "HOLIDAY_ADDITIONAL",
    "SPECIAL_NOTICE", "CANCELLED",
}
HEX = set("0123456789abcdef")


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def canonical(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf8")


def _time(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ExternalProofRefused("explicit timezone-aware schedule timestamp required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise ExternalProofRefused("invalid schedule timestamp") from exc


def _url(value: str):
    if not isinstance(value, str):
        raise ExternalProofRefused("MOEX original document URL missing")
    try:
        u = urlsplit(value)
        valid = (
            u.scheme == "https"
            and u.hostname in {"moex.com", "www.moex.com"}
            and u.port is None and u.username is None and u.password is None
            and u.path.startswith("/") and not u.fragment
            and all(ch not in value for ch in "\n\r\t\\")
        )
    except ValueError:
        valid = False
    if not valid:
        raise ExternalProofRefused("original source is not official HTTPS MOEX hostname")


def _hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(ch not in HEX for ch in value):
        raise ExternalProofRefused("invalid immutable schedule SHA")


def _raw_snapshot(doc: dict, issued: datetime) -> bytes:
    if not isinstance(doc, dict):
        raise ExternalProofRefused("malformed MOEX source document metadata")
    _url(doc.get("url"))
    if doc.get("content_type") not in ALLOWED_CONTENT_TYPES:
        raise ExternalProofRefused("unsupported original MOEX schedule content type")
    observed = _time(doc.get("observed_at_utc"))
    if observed > issued:
        raise ExternalProofRefused("schedule source not observed before forecast T0")
    _hash(doc.get("sha256"))
    encoded = doc.get("body_b64")
    if not isinstance(encoded, str) or not encoded:
        raise ExternalProofRefused("missing exact original MOEX schedule bytes")
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ExternalProofRefused("malformed original MOEX document base64") from exc
    if not 0 < len(body) <= MAX_RAW_DOCUMENT or digest(body) != doc["sha256"]:
        raise ExternalProofRefused("MOEX original schedule bytes do not match declared digest")
    return body


def verified_schedule_candidate(
    envelope_bytes: bytes, *, forecast_id: str, instrument: dict,
    forecast_t0: str,
) -> dict:
    """Require immutable original docs and complete ordered revisions as-of T0.

    Each revision points to an exact archived document; its extracted session
    values are still only a claim, never an authenticated official session.
    """
    if not isinstance(envelope_bytes, bytes) or not 0 < len(envelope_bytes) <= MAX_ENVELOPE:
        raise ExternalProofRefused("bounded original schedule witness required")
    try:
        evidence = json.loads(envelope_bytes)
        if canonical(evidence) != envelope_bytes:
            raise ValueError("noncanonical signed-off local schedule evidence")
        if evidence.get("schema") != SCHEMA or evidence.get("forecast_id") != forecast_id:
            raise ValueError("schedule not bound to exact Forecast")
        if not isinstance(instrument, dict) or any(
            not isinstance(instrument.get(k), str) or not instrument[k]
            for k in ("secid", "engine", "market", "board")
        ):
            raise ValueError("unknown original Forecast instrument")
        if evidence.get("instrument") != {
            k: instrument[k] for k in ("secid", "engine", "market", "board")
        }:
            raise ValueError("schedule instrument/board mismatches source")
        issued = _time(forecast_t0)
        docs = evidence["source_documents"]
        revisions = evidence["revisions"]
        if not isinstance(docs, list) or not 1 <= len(docs) <= MAX_DOCS:
            raise ValueError("original official document inventory missing")
        if not isinstance(revisions, list) or not 1 <= len(revisions) <= MAX_REVISIONS:
            raise ValueError("original schedule version chain missing")
        observed_docs = {}
        for doc in docs:
            _raw_snapshot(doc, issued)
            fingerprint = doc["sha256"]
            if fingerprint in observed_docs:
                raise ValueError("duplicate official document digest")
            observed_docs[fingerprint] = _time(doc["observed_at_utc"])
        referenced_documents = set()
        previous_hash = None
        previous_published = None
        previous_observed = None
        current = None
        for index, revision in enumerate(revisions, 1):
            if not isinstance(revision, dict):
                raise ValueError("invalid revision")
            if revision.get("revision_number") != index or type(revision["revision_number"]) is not int:
                raise ValueError("noncontiguous schedule revision")
            if revision.get("parent_revision_sha256") != previous_hash:
                raise ValueError("broken immutable revision hash chain")
            source = revision.get("source_document_sha256")
            if source not in observed_docs:
                raise ValueError("revision refers to unretained MOEX original")
            referenced_documents.add(source)
            published = _time(revision.get("published_at_utc"))
            captured = observed_docs[source]
            if published > captured or captured > issued:
                raise ValueError("retroactively discovered schedule notice")
            if previous_published is not None and published <= previous_published:
                raise ValueError("unordered schedule notices")
            if previous_observed is not None and captured < previous_observed:
                raise ValueError("source receipt order contradicts revision")
            if revision.get("session_identifier") != evidence.get("session_identifier"):
                raise ValueError("revision changes identity of forecast session")
            session_kind = revision.get("session_kind")
            if session_kind not in SESSION_KINDS:
                raise ValueError("unknown altered trading session type")
            if revision.get("instrument_eligible") is not True:
                raise ValueError("specific SECID trading eligibility missing")
            session = date.fromisoformat(revision["session_calendar_date_msk"])
            trading_day = date.fromisoformat(revision["assigned_trading_day_msk"])
            if (trading_day - session).days not in (0, 1, 2, 3):
                raise ValueError("unsupported settlement/trading-day assignment")
            first = _time(revision["first_future_event_utc"])
            closed = _time(revision["session_close_utc"])
            if first.astimezone(MOEX_MSK).date() != session or not first < closed:
                raise ValueError("event outside its actual Moscow session date")
            if (closed - first).total_seconds() > 24 * 3600:
                raise ValueError("impossible single trading session duration")
            if session_kind == "CANCELLED":
                current = None
            else:
                current = revision
            previous_hash = digest(canonical(revision))
            previous_published, previous_observed = published, captured
        if referenced_documents != set(observed_docs):
            raise ValueError("unreviewed schedule document has no linked revision")
        if current is None:
            raise ValueError("latest official notice cancels this session")
        first = _time(current["first_future_event_utc"])
        if first <= issued:
            raise ValueError("session event not prospective at Forecast T0")
        # This proves only that the operator's extracted candidate refers to
        # retained source bytes and all revisions were known by claimed T0.
        return {
            "schema": CUTOFF_SCHEMA,
            "secid": instrument["secid"],
            "engine": instrument["engine"],
            "market": instrument["market"],
            "board": instrument["board"],
            "cutoff_kind": "FIRST_FUTURE_MARKET_EVENT",
            "session_identifier": evidence["session_identifier"],
            "earliest_event_utc": first.isoformat().replace("+00:00", "Z"),
            "external_schedule_snapshot_sha256": digest(envelope_bytes),
            "effective_revision_sha256": previous_hash,
            "official_domain_claim_checked": True,
            "original_source_bytes_archived": True,
            "notice_inventory_completeness_independently_verified": False,
            "published_at_time_independently_authenticated": False,
            "source_identity_cryptographically_authenticated": False,
            "instrument_eligibility_independently_certified": False,
            "session_event_time_independently_certified": False,
            "exchange_calendar_release_approved": False,
        }
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, ExternalProofRefused):
            raise
        raise ExternalProofRefused("original versioned official schedule witness inconsistent") from exc


def derived_candidate_from_staging(store, forecast_id: str) -> dict:
    """Require matching canonical Forecast/source manifest and archived notice."""
    from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
    if not isinstance(store, SingleDuckDBProspectiveStagingJournal):
        raise ExternalProofRefused("immutable G2 single-DuckDB staging journal required")
    with store.lock:
        store._audit()
        row = store.db.execute(
            "SELECT f.payload_json, c.receipt_json FROM forecast_records f "
            "JOIN g2_atomic_captures c ON f.forecast_id=c.forecast_id "
            "WHERE f.forecast_id=?", [forecast_id],
        ).fetchone()
        if row is None:
            raise ExternalProofRefused("forecast/source evidence not found")
        forecast = json.loads(row[0])
        original = store.read_original_source_manifest(forecast_id)
        if original is None:
            raise ExternalProofRefused("legacy original market manifest not available")
        manifest = json.loads(original)
        records = [x for x in manifest["instruments"] if isinstance(x, dict)
                   and x.get("resolved_secid") == forecast["secid"]
                   and x.get("status") == "SOURCE_CAPTURE_COMPLETE"]
        if len(records) != 1:
            raise ExternalProofRefused("exact instrument ambiguous")
        envelope = store.db.execute(
            "SELECT schedule_blob FROM g2_atomic_schedule_evidence WHERE forecast_id=?",
            [forecast_id],
        ).fetchone()
        if envelope is None:
            raise ExternalProofRefused("no original official schedule witness retained")
        return verified_schedule_candidate(
            bytes(envelope[0]), forecast_id=forecast_id,
            instrument=records[0]["instrument"], forecast_t0=forecast["created_at_t0"],
        )
