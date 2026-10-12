"""Q-097: read-only exact provenance-link audit with canonical release gate.

A successful mock research chain is NOT release authorization. This
decision reads real canonical blocker states; no manifest edits or deploy.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json

from birzha.application.g2_m1_first_intake import OriginalM1LocalIntake, HANDOFF
from birzha.application.g2_m15_segmented_session_guard import check_normalized_session_segments
from birzha.application.g2_m15_staging_preflight import SessionNoticeCandidate
from birzha.domain.market import Instrument, Candle
from birzha.application.g2_governed_m1_intake import VERSION as CAPTURE_SCHEMA
from birzha.application.g2_m15_staging_preflight import (
    VERSION as PREFLIGHT_SCHEMA, _json, _sha,
)
from birzha.application.g2_m15_segmented_session_guard import (
    VERSION as SEGMENT_SCHEMA,
)
from scripts.g2_release_gate import validate

VERSION = "G2_SOURCE_CHAIN_NO_GO_AUDIT_V1"


class SourceChainAuditRefused(ValueError):
    """Research admission data contradictory, swapped or over-asserted."""


def audit_source_chain_no_go(
    *, governed_capture: dict, preflight: dict,
    segmented_session: dict, release_manifest: dict,
    intake: OriginalM1LocalIntake, capture_key: str,
    instrument: Instrument, candidate: Candle,
    session_notice: SessionNoticeCandidate, evaluated_at: datetime,
) -> dict:
    """Bind three research layers to the authoritative 12 release blockers.

    Recomputes Q-088/Q-096 from the actual Q-090 SQLite source snapshot.
    The caller cannot supply only internally rehashed manifest summaries.
    Returns read-only NO-GO, never production authority.
    """
    if any(type(x) is not dict for x in (
        governed_capture, preflight, segmented_session, release_manifest
    )):
        raise SourceChainAuditRefused("complete typed research evidence required")
    if (type(intake) is not OriginalM1LocalIntake
        or not isinstance(instrument, Instrument)
        or not isinstance(candidate, Candle)
        or not isinstance(session_notice, SessionNoticeCandidate)):
        raise SourceChainAuditRefused("original journal, instrument and notice required")
    try:
        # A caller-supplied release JSON is NOT an authoritative manifest.
        # For this frozen research-only gate, compare with actual pinned
        # checkout contents before interpreting any status.
        canonical_path = Path(__file__).resolve().parents[3] / "docs/G2_RELEASE_GATE_20261010.json"
        with canonical_path.open(encoding="utf-8") as handle:
            canonical_release = json.load(handle)
        if release_manifest != canonical_release:
            raise SourceChainAuditRefused("caller release manifest differs from repository canonical file")
        release = validate(release_manifest)
        if (release["release_allowed"] or release["release_state"] != "RELEASE_BLOCKED"
            or release_manifest.get("user_approved_main_merge") is not False
            or release_manifest.get("user_approved_home_deploy") is not False
            or release["gates_passed"] != 1
            or len(release["blocking_requirements"]) != 12):
            # Q-097 must never silently accept forged partial user approvals
            # or count newly asserted PASS gates without fresh adjudication.
            raise SourceChainAuditRefused("canonical 12-blocker research no-go changed")
        if (governed_capture["schema"] != CAPTURE_SCHEMA
            or governed_capture["status"] != "SANDBOX_RESPONSE_CALLBACK_LOCALLY_CAPTURED"
            or governed_capture["real_moex_transport_used"] is not False
            or governed_capture["fixture_transport_source_only"] is not True
            or governed_capture["level_journal_admission_authorized"] is not False
            or governed_capture["production_authorized"] is not False):
            raise SourceChainAuditRefused("capture is not a trusted mock-only receipt")
        if (preflight["schema"] != PREFLIGHT_SCHEMA
            or preflight["status"] !=
               "OFFLINE_CONTENT_PRECHECK_PASS_EXTERNAL_ADMISSION_BLOCKED"
            or preflight["level_journal_admission_authorized"] is not False
            or preflight["production_authorized"] is not False
            or type(preflight["commitment_bytes"]) is not bytes
            or preflight["commitment_bytes"] != _json(preflight["manifest"])
            or _sha(preflight["commitment_bytes"]) != preflight["commitment_sha256"]):
            raise SourceChainAuditRefused("Q-088 source commitment invalid")
        if (segmented_session["schema"] != SEGMENT_SCHEMA
            or segmented_session["status"] !=
                "CLAIMED_SEGMENT_CONTENT_PASS_EXTERNAL_CALENDAR_BLOCKED"
            or segmented_session["level_journal_admission_authorized"] is not False
            or segmented_session["production_authorized"] is not False
            or _sha(_json(segmented_session["manifest"])) !=
               segmented_session["commitment_sha256"]
            or segmented_session["manifest"]["original_q088_sha256"] !=
               preflight["commitment_sha256"]
            or segmented_session["manifest"]["market_session_independently_authenticated"] is not False):
            raise SourceChainAuditRefused("session chain swapped or over-asserted")
        # Re-open a self-consistent Q-090 snapshot and rerun M1 -> M15
        # before trusting any staged SHA or the caller's Q-096 result.
        local = intake.original_capture_handoff(
            capture_key=capture_key, instrument=instrument,
            candidate=candidate, session_notice=session_notice,
            evaluated_at=evaluated_at)
        if (local["schema"] != HANDOFF
            or local["status"] != "LOCAL_INTAKE_AND_M15_CONTENT_BOUND_EXTERNAL_GATE_BLOCKED"
            or local["new_writes"] != 0
            or local["level_journal_admission_authorized"] is not False
            or local["production_authorized"] is not False
            or local["handoff_bytes"] !=
               (_json(local["manifest"]))
            or _sha(local["handoff_bytes"]) != local["handoff_sha256"]
            or local["q088"] != preflight
            or local["manifest"]["q088_original_commitment_sha256"] !=
               preflight["commitment_sha256"]):
            raise SourceChainAuditRefused("original locally stored M1 cannot reproduce staged Q-088")
        canonical_segments = check_normalized_session_segments(
            preflight=local["q088"], instrument=instrument, notice=session_notice)
        if canonical_segments != segmented_session:
            raise SourceChainAuditRefused("claimed session segments do not reproduce original notice")
        instrument_identity = preflight["manifest"]["instrument"]
        if governed_capture["instrument"] != "/".join(instrument_identity[k] for k in
                                                     ("engine","market","board","secid")):
            raise SourceChainAuditRefused("market identity differs across source layers")
        pages=preflight["manifest"]["m1_source_pages"]
        if (type(pages) is not list or not pages
            or not any(
                p["original_bytes_sha256"] == governed_capture["original_raw_sha256"]
                and p["page_offset"] == governed_capture["page_start"]
                and p["request_url_claim"] == governed_capture["request_url_claim"]
                for p in pages
            )):
            raise SourceChainAuditRefused("governed source body/route absent from M15 replay")
        if (segmented_session["manifest"]["original_normalized_notice_sha256"] !=
            preflight["manifest"]["notice_candidate"]["notice_original_bytes_sha256"]
            or segmented_session["manifest"]["exact_instrument"] != instrument_identity):
            raise SourceChainAuditRefused("calendar notice/SECID not bound to source")
        original_receipts = local["manifest"]["original_receipts"]
        origin_receipt = governed_capture["receipt"]
        if (governed_capture["capture_key"] != capture_key
            or local["manifest"]["capture_key"] != capture_key
            or governed_capture["receipt"]["original_bytes_sha256"] !=
               governed_capture["original_raw_sha256"]
            or not any(
                r["seq"] == origin_receipt["receipt_seq"]
                and r["local_received_utc"] == origin_receipt["first_local_received_utc"]
                and r["raw_sha256"] == governed_capture["original_raw_sha256"]
                for r in original_receipts
            )
            or not any(
                page["page_offset"] == governed_capture["page_start"]
                and page["original_bytes_sha256"] == governed_capture["original_raw_sha256"]
                and page["response_received_utc_claim"] ==
                    governed_capture["callback_response_utc"]
                for page in pages
            )):
            raise SourceChainAuditRefused("original first local receipt not attached to raw source")
        flags=(
            "external_source_authenticated",
            "verified_first_network_receipt",
            "exchange_calendar_authenticated",
            "trusted_independent_time_attestation",
            "live_level_event_admission",
            "forecast_or_trade_authorized",
            "production_authorized",
        )
        return {
            "schema":VERSION,
            "status":"RESEARCH_CHAIN_CONTENT_PASS_PRODUCTION_NO_GO",
            "source_m1_sha256":governed_capture["original_raw_sha256"],
            "q088_commitment_sha256":preflight["commitment_sha256"],
            "q090_local_handoff_sha256":local["handoff_sha256"],
            "session_segment_commitment_sha256":segmented_session["commitment_sha256"],
            "release_gate_schema":release["schema"],
            "release_allowed":False,
            "unresolved_blocker_count":len(release["blocking_requirements"]),
            "unresolved_blocker_names":[x["gate"] for x in release["blocking_requirements"]],
            "unverified_required_facts":list(flags),
            "main_merge_authorized":False,
            "home_deploy_authorized":False,
            "production_authorized":False,
        }
    except (KeyError,TypeError,ValueError,AttributeError,OSError,RuntimeError) as exc:
        raise SourceChainAuditRefused("source chain or release gate mismatch") from exc
