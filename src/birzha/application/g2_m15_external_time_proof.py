"""Q-089: RFC3161 offline proof for Q-088 source M15 content commitment.

Validates original-source replay again, then two independent OpenSSL
signature checks (nonce/query and SHA256/data) on the SAME TSA response.
This does not authenticate MOEX transport, independent original receipt T0,
source calendar or the caller's CA trust decision. No live TSA calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from typing import Callable

from birzha.application.g2_m15_staging_preflight import (
    VERSION as PREFLIGHT_SCHEMA, _time, prepare_m15_staging_preflight,
)
from birzha.domain.market import Candle, Instrument
from birzha.application.g2_m1_m15_source_replay import OriginalM1Page
from birzha.application.g2_m15_staging_preflight import SessionNoticeCandidate

VERSION = "G2_M15_RFC3161_OFFLINE_PROOF_V1"
SIGNED_TIME = re.compile(
    r"^\s*Time stamp:\s*([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s+GMT)\s*$",
    re.MULTILINE,
)
NONCE = re.compile(r"^\s*Nonce:\s*0x[0-9A-Fa-f]{16,}\s*$", re.MULTILINE)
MAX_ARTIFACT_BYTES = 1_000_000


class M15TimeProofRefused(ValueError):
    """Trusted offline proof is absent; no unverified promotion permitted."""


def _file(path: Path) -> Path:
    if not isinstance(path, Path):
        raise M15TimeProofRefused("explicit original TSA file path required")
    try:
        if (path.is_symlink() or not path.is_file() or
            not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES):
            raise M15TimeProofRefused("missing, symlinked or invalid TSA input")
    except OSError as exc:
        raise M15TimeProofRefused("unreadable original TSA input") from exc
    return path


def verify_m15_rfc3161(
    *, preflight: dict,
    instrument: Instrument, pages: tuple[OriginalM1Page, ...],
    candidate: Candle, session_notice: SessionNoticeCandidate,
    query_path: Path, response_path: Path, trust_ca_path: Path,
    untrusted_chain_path: Path | None = None,
    openssl: str = "openssl", runner: Callable = subprocess.run,
) -> dict:
    """Verify one *existing* signed preflight packet; never generate a token.

    Repeat full source/session replay at the ORIGINAL claimed preflight time.
    Caller-provided fake runner is only for offline unit tests; real trust
    needs openssl and separately approved CA + retained original artifacts.
    """
    if not isinstance(preflight, dict):
        raise M15TimeProofRefused("typed Q-088 preflight packet required")
    try:
        original = preflight["commitment_bytes"]
        sha = preflight["commitment_sha256"]
        manifest = preflight["manifest"]
        if (not isinstance(original, bytes) or not original or
            not isinstance(sha, str) or sha256(original).hexdigest() != sha or
            not isinstance(manifest, dict) or
            manifest.get("schema") != PREFLIGHT_SCHEMA or
            preflight.get("schema") != PREFLIGHT_SCHEMA or
            preflight.get("status") !=
              "OFFLINE_CONTENT_PRECHECK_PASS_EXTERNAL_ADMISSION_BLOCKED" or
            preflight.get("level_journal_admission_authorized") is not False or
            preflight.get("production_authorized") is not False):
            raise ValueError("Q-088 packet is not unchanged and unapproved")
        issued = _time(manifest["preflight_evaluated_at_local_clock_utc"])
        repeated = prepare_m15_staging_preflight(
            instrument=instrument, pages=pages, candidate=candidate,
            session_notice=session_notice, clock=lambda: issued)
        if (original != repeated["commitment_bytes"] or sha != repeated["commitment_sha256"]
            or manifest != repeated["manifest"] or
            preflight["source_page_count"] != repeated["source_page_count"]):
            raise ValueError("original Q-088 source/session packet changed")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise M15TimeProofRefused("original Q-088 commitment source binding failed") from exc

    qfile, rfile, ca = _file(query_path), _file(response_path), _file(trust_ca_path)
    intermediate = _file(untrusted_chain_path) if untrusted_chain_path is not None else None
    if not isinstance(openssl, str) or not openssl.strip():
        raise M15TimeProofRefused("OpenSSL verifier unavailable")

    def checked(argv: list[str]) -> str:
        try:
            result = runner(argv, input=None, capture_output=True,
                            check=False, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise M15TimeProofRefused("external offline verifier unavailable") from exc
        if getattr(result, "returncode", None) != 0:
            raise M15TimeProofRefused("TSA nonce/signature/data imprint verification failed")
        try:
            return result.stdout.decode("utf-8")
        except (AttributeError, UnicodeError) as exc:
            raise M15TimeProofRefused("malformed verifier output") from exc

    query = checked([openssl,"ts","-query","-in",str(qfile),"-text"])
    if NONCE.search(query) is None:
        raise M15TimeProofRefused("original RFC3161 query lacks adequate nonce")
    trust = ["-CAfile",str(ca)]
    if intermediate is not None:
        trust += ["-untrusted",str(intermediate)]
    base = [openssl,"ts","-verify","-in",str(rfile),*trust]
    checked([*base,"-queryfile",str(qfile)])
    checked([*base,"-digest",sha])
    details = checked([openssl,"ts","-reply","-in",str(rfile),"-text"])
    signed = SIGNED_TIME.search(details)
    if not signed:
        raise M15TimeProofRefused("signed TSA time missing")
    try:
        at = datetime.strptime(
            " ".join(signed.group(1).split()), "%b %d %H:%M:%S %Y GMT"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise M15TimeProofRefused("invalid signed TSA timestamp") from exc
    # TSA proves commitment existed BY signed time; it never proves first
    # observation happened at that same time, nor true market provenance.
    if not issued.replace(microsecond=0) <= at <= issued + timedelta(seconds=300):
        raise M15TimeProofRefused("TSA time outside post-preflight commitment window")
    return {
        "schema":VERSION,
        "status":"SIGNED_Q088_COMMITMENT_CHECKED_UNDER_CALLER_CA",
        "m15_preflight_commitment_sha256":sha,
        "tsa_signed_time_utc":at.isoformat().replace("+00:00","Z"),
        "nonce_query_signature_and_data_imprint_checked":True,
        "external_ca_independently_approved":False,
        "earliest_original_market_receipt_proven":False,
        "independent_moex_provider_authentication":False,
        "official_session_calendar_independently_verified":False,
        "future_outcome_boundary_certified":False,
        "level_journal_admission_authorized":False,
        "forecast_or_trade_authorized":False,
        "production_authorized":False,
    }
