"""Q-097: read-only exact provenance-link audit with canonical release gate.

A successful mock research chain is NOT release authorization. This
decision reads real canonical blocker states; no manifest edits or deploy.
"""
from __future__ import annotations

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
) -> dict:
    """Bind three research layers to the authoritative 12 release blockers.

    Returns read-only NO-GO output even after valid synthetic fixture tests.
    """
    if any(type(x) is not dict for x in (
        governed_capture, preflight, segmented_session, release_manifest
    )):
        raise SourceChainAuditRefused("complete typed research evidence required")
    try:
        release = validate(release_manifest)
        if release["release_allowed"] or release["release_state"] != "RELEASE_BLOCKED":
            raise SourceChainAuditRefused("production-ready manifest not a research no-go")
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
        instrument = preflight["manifest"]["instrument"]
        if governed_capture["instrument"] != "/".join(instrument[k] for k in
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
            or segmented_session["manifest"]["exact_instrument"] != instrument):
            raise SourceChainAuditRefused("calendar notice/SECID not bound to source")
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
    except (KeyError,TypeError,ValueError,AttributeError) as exc:
        raise SourceChainAuditRefused("source chain or release gate mismatch") from exc
