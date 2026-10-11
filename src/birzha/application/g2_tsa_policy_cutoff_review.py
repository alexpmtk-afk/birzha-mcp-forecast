"""OFFLINE review of an archived RFC3161 proof and independently supplied event cutoff.

This module is deliberately NOT an approval endpoint. An arbitrary JSON
document cannot certify a TSA, a CA, or a MOEX exchange schedule. Even a
matching fingerprint/policy and correctly ordered token leave release BLOCKED.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import re
import subprocess
from typing import Callable

from birzha.application.g2_time_anchor import ExternalProofRefused, verify_rfc3161
from birzha.storage.g2_timestamp_evidence import read_signed_time_evidence


SHA256 = re.compile(r"^[0-9a-f]{64}$")
OID = re.compile(r"^[0-2](?:\.[0-9]+){2,}$")
POLICY = re.compile(r"^\s*Policy OID:\s*([0-9]+(?:\.[0-9]+)+)\s*$", re.MULTILINE)
ACCURACY = re.compile(r"^\s*Accuracy:\s*([^\r\n]+)$", re.MULTILINE)
ACCURACY_PART = re.compile(r"(0x[0-9a-fA-F]+|[0-9]+)\s+(seconds?|millis|micros)\b", re.IGNORECASE)
REVIEW_SCHEMA = "G2_RFC3161_CANDIDATE_TRUST_REVIEW_V1"
CUTOFF_SCHEMA = "G2_EXACT_SECID_FUTURE_EVENT_CUTOFF_CANDIDATE_V1"


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ExternalProofRefused("explicit offset-aware event cutoff required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("missing UTC offset")
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ExternalProofRefused("invalid UTC event cutoff") from exc


def _accuracy_ms(openssl_text: str) -> int:
    found = ACCURACY.search(openssl_text)
    if not found:
        raise ExternalProofRefused("TSA accuracy not provided in token")
    parts = ACCURACY_PART.findall(found.group(1))
    if not parts or len(parts) > 3:
        raise ExternalProofRefused("TSA accuracy unknown")
    values = {}
    for raw, unit in parts:
        unit = "seconds" if unit.lower().startswith("second") else unit.lower()
        if unit in values:
            raise ExternalProofRefused("duplicate TSA accuracy field")
        values[unit] = int(raw, 16 if raw.startswith(("0x", "0X")) else 10)
    if any(values.get(unit, 0) < 0 for unit in ("seconds", "millis", "micros")):
        raise ExternalProofRefused("negative TSA accuracy")
    if values.get("millis", 0) > 999 or values.get("micros", 0) > 999:
        raise ExternalProofRefused("invalid TSA accuracy units")
    microseconds = (
        values.get("seconds", 0)*1_000_000
        + values.get("millis", 0)*1_000
        + values.get("micros", 0)
    )
    # Ceil to one millisecond: never round the uncertainty down.
    if not 0 < microseconds <= 60_000_000:
        raise ExternalProofRefused("TSA accuracy zero, missing or unbounded")
    return (microseconds + 999)//1000


def _policy_oid(openssl_text: str) -> str:
    match = POLICY.search(openssl_text)
    if not match or not OID.fullmatch(match.group(1)):
        raise ExternalProofRefused("signed token policy OID unavailable")
    return match.group(1)


def _verified_reply_text(*, response: Path, runner: Callable, openssl: str) -> str:
    try:
        proc=runner(
            [openssl, "ts", "-reply", "-in", str(response), "-text"],
            input=None, capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExternalProofRefused("offline signed token metadata unavailable") from exc
    if proc.returncode != 0:
        raise ExternalProofRefused("signed token metadata unavailable")
    return proc.stdout.decode("utf8", errors="replace")


def conditional_before_event_review(
    store, forecast_id: str, *, trust_candidate: dict,
    event_cutoff_candidate: dict, runner: Callable = subprocess.run,
    openssl: str = "openssl",
) -> dict:
    """Reverify *saved* TSQ/TSR/CA bytes, then return a non-authorizing review.

    Required external policies and event cutoffs are explicitly caller-supplied
    and unauthenticated by this function. Never return a release PASS.
    """
    from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
    if not isinstance(store, SingleDuckDBProspectiveStagingJournal):
        raise ExternalProofRefused("canonical single-DuckDB staging required")
    if not isinstance(trust_candidate, dict) or trust_candidate.get("schema") != REVIEW_SCHEMA:
        raise ExternalProofRefused("unreviewed TSA trust policy")
    if not isinstance(event_cutoff_candidate, dict) or event_cutoff_candidate.get("schema") != CUTOFF_SCHEMA:
        raise ExternalProofRefused("unverified trading session cutoff candidate")
    allowed=trust_candidate.get("acceptable_tsa_policy_oids")
    ca_digest=trust_candidate.get("reviewed_ca_bundle_sha256")
    max_accuracy=trust_candidate.get("max_tsa_accuracy_ms")
    if (not isinstance(allowed, list) or not allowed
        or any(not isinstance(x, str) or not OID.fullmatch(x) for x in allowed)
        or not isinstance(ca_digest, str) or not SHA256.fullmatch(ca_digest)
        or type(max_accuracy) is not int or not 0 < max_accuracy <= 10_000):
        raise ExternalProofRefused("incomplete pinned TSA candidate profile")

    retained=read_signed_time_evidence(store,forecast_id)
    if retained is None:
        raise ExternalProofRefused("no original externally signed evidence stored")
    if sha256(retained["caller_supplied_ca"]).hexdigest()!=ca_digest:
        raise ExternalProofRefused("caller CA bytes differ from pinned review candidate")
    statement=json.loads(retained["original_commitment"])
    if statement.get("forecast_id") != forecast_id:
        raise ExternalProofRefused("timestamp commitment does not belong to Forecast")
    exact_secid=statement["secid"]

    # Exact route comes from the original audited provider manifest, never an
    # event-cutoff caller's claim about which market this Forecast belongs to.
    with store.lock:
        original=store.read_original_source_manifest(forecast_id)
    if original is None:
        raise ExternalProofRefused("original market route evidence unavailable")
    manifest=json.loads(original)
    matches=[e for e in manifest.get("instruments", []) if isinstance(e,dict)
             and e.get("resolved_secid")==exact_secid]
    if len(matches)!=1:
        raise ExternalProofRefused("ambiguous exact forecast instrument")
    instrument=matches[0]["instrument"]
    for name in ("secid","engine","market","board"):
        expected=exact_secid if name=="secid" else instrument.get(name)
        if event_cutoff_candidate.get(name)!=expected:
            raise ExternalProofRefused("future cutoff instrument route mismatch")
    if event_cutoff_candidate.get("cutoff_kind") != "FIRST_FUTURE_MARKET_EVENT":
        raise ExternalProofRefused("future event cutoff type not supported")
    if not isinstance(event_cutoff_candidate.get("session_identifier"), str) or not event_cutoff_candidate["session_identifier"].strip():
        raise ExternalProofRefused("explicit future session identifier required")
    snap=event_cutoff_candidate.get("external_schedule_snapshot_sha256")
    if not isinstance(snap, str) or not SHA256.fullmatch(snap):
        raise ExternalProofRefused("original schedule document SHA required")
    cutoff=_utc(event_cutoff_candidate.get("earliest_event_utc"))

    with TemporaryDirectory(prefix="g2-time-review-") as tmp:
        root=Path(tmp)
        for name,blob in (
            ("query.tsq",retained["original_query"]),
            ("response.tsr",retained["signed_response"]),
            ("ca.pem",retained["caller_supplied_ca"]),
            ("chain.pem",retained["untrusted_chain"]),
        ):
            if blob is not None:
                (root/name).write_bytes(blob)
        proof=verify_rfc3161(
            retained["original_commitment"],
            issued_at_t0=statement["forecast_t0_claimed"],
            query_path=root/"query.tsq",
            response_path=root/"response.tsr",
            trust_ca_path=root/"ca.pem",
            untrusted_chain_path=(root/"chain.pem" if retained["untrusted_chain"] else None),
            runner=runner,openssl=openssl,
        )
        details=_verified_reply_text(response=root/"response.tsr", runner=runner,openssl=openssl)
    oid=_policy_oid(details)
    if oid not in allowed:
        raise ExternalProofRefused("TSA token policy OID not in approved candidate set")
    accuracy=_accuracy_ms(details)
    if accuracy > max_accuracy:
        raise ExternalProofRefused("TSA signed time uncertainty exceeds candidate limit")
    signed=_utc(proof["tsa_signed_time_utc"])
    upper=signed+timedelta(milliseconds=accuracy)
    if upper>=cutoff:
        raise ExternalProofRefused("upper TSA time uncertainty is not before future event cutoff")
    # A user-supplied schedule/CA pin is not independent validation of either.
    return {
        "schema":"G2_RFC3161_CONDITIONAL_ORDERING_REVIEW_V1",
        "status":"CANDIDATE_CRYPTO_AND_TIME_ORDER_CONSISTENT",
        "forecast_id":forecast_id,
        "secid":exact_secid,
        "timestamp_commitment_sha256":sha256(retained["original_commitment"]).hexdigest(),
        "tsa_policy_oid":oid,
        "tsa_signed_time_utc":proof["tsa_signed_time_utc"],
        "tsa_accuracy_upper_bound_ms":accuracy,
        "earliest_future_event_cutoff_candidate_utc":cutoff.isoformat().replace("+00:00","Z"),
        "reviewed_ca_bundle_sha256":ca_digest,
        "external_schedule_snapshot_sha256":snap,
        "independent_tsa_authority_approved":False,
        "exchange_session_cutoff_independently_certified":False,
        "local_forecast_t0_independently_measured":False,
        "future_outcome_cutoff_independently_approved":False,
        "production_authorized":False,
        "release_gate_can_close":False,
    }
