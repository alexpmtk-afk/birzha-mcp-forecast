"""Opt-in RFC 3161 independent proof-of-existence verification for G2.

No default TSA, no hidden network access and no trust anchors bundled here.
A valid signed timestamp proves commitment to supplied immutable bytes existed
at a TSA time; it DOES NOT certify truth of the underlying MOEX source, exchange
calendar, local T0 or predictive skill. This never unlocks the release gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from typing import Callable


VERSION = "G2_RFC3161_FORECAST_COMMITMENT_V1"
GEN_TIME = re.compile(r"^\s*Time stamp:\s*([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s+GMT)\s*$", re.MULTILINE)
HEX_64 = re.compile(r"^[a-f0-9]{64}$")


class ExternalProofRefused(ValueError):
    """Safe exception with no token contents, endpoint URL or CA path."""


def _datetime(stamp: str) -> datetime:
    if not isinstance(stamp, str):
        raise ExternalProofRefused("invalid Forecast issue timestamp")
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ExternalProofRefused("invalid Forecast issue timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ExternalProofRefused("issue timestamp requires UTC offset")
    return dt.astimezone(timezone.utc)


def original_forecast_commitment(
    forecast_json: bytes, source_blob: bytes, original_manifest: bytes,
) -> bytes:
    """Deterministically bind *actual immutable Forecast* to the original source."""
    if not all(isinstance(v, bytes) and v for v in
               (forecast_json, source_blob, original_manifest)):
        raise ExternalProofRefused("complete original Forecast/source bytes required")
    try:
        record = json.loads(forecast_json)
        manifest = json.loads(original_manifest)
        fid = record["forecast_id"]
        secid = record["secid"]
        t0 = record["created_at_t0"]
        if (not isinstance(fid, str) or not fid or not isinstance(secid, str)
            or not secid or manifest.get("schema") != "G2_SIX_MARKET_LIVE_SOURCE_RECEIPT_V1"):
            raise ValueError("unbound forecast/original manifest")
        if not any(
            e.get("resolved_secid") == secid and e.get("status") == "SOURCE_CAPTURE_COMPLETE"
            for e in manifest.get("instruments", []) if isinstance(e, dict)
        ):
            raise ValueError("source manifest does not contain forecast SECID")
        _datetime(t0)
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ExternalProofRefused("invalid immutable G2 forecast/manifest input") from exc
    statement = {
        "schema": VERSION,
        "forecast_id": fid,
        "secid": secid,
        "forecast_t0_claimed": t0,
        "forecast_payload_sha256": sha256(forecast_json).hexdigest(),
        "original_source_blob_sha256": sha256(source_blob).hexdigest(),
        "original_source_manifest_sha256": sha256(original_manifest).hexdigest(),
        "external_time_attestation": "REQUIRES_RFC3161_SIGNED_RESPONSE",
    }
    return (json.dumps(statement, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False)+"\n").encode("utf8")


def commitment_from_atomic_staging(store, forecast_id: str) -> bytes:
    """Anchor only material independently audited in one canonical DuckDB."""
    from birzha.storage.prospective_single_store import (
        SingleDuckDBProspectiveStagingJournal,
    )
    if not isinstance(store, SingleDuckDBProspectiveStagingJournal):
        raise ExternalProofRefused("explicit atomic staging writer required")
    with store.lock:
        store._audit()
        row = store.db.execute(
            """SELECT f.payload_json, c.source_blob
            FROM forecast_records f JOIN g2_atomic_captures c
            ON f.forecast_id=c.forecast_id WHERE f.forecast_id=?""",
            [forecast_id],
        ).fetchone()
        if row is None:
            raise ExternalProofRefused("no already persisted prospective Forecast")
        original_manifest = store.read_original_source_manifest(forecast_id)
        if original_manifest is None:
            raise ExternalProofRefused("legacy Forecast has no original page manifest")
        return original_forecast_commitment(
            row[0].encode("utf8"), bytes(row[1]), original_manifest,
        )


def verify_rfc3161(
    commitment: bytes, *, issued_at_t0: str, query_path: Path,
    response_path: Path, trust_ca_path: Path, 
    untrusted_chain_path: Path | None = None,
    openssl: str = "openssl",
    runner: Callable = subprocess.run,
) -> dict:
    """Require BOTH signature+nonce(query) AND signature+data imprint checks.

    Caller provides separately approved CA trust; no self-issued trust or
    unauthenticated server Date is silently accepted as a source of truth.
    """
    if not isinstance(commitment, bytes) or not commitment:
        raise ExternalProofRefused("nonempty immutable commitment required")
    try:
        parsed = json.loads(commitment)
        if parsed.get("schema") != VERSION or parsed.get("forecast_t0_claimed") != issued_at_t0:
            raise ValueError("unbound commitment")
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ExternalProofRefused("timestamp statement/T0 mismatch") from exc
    issue = _datetime(issued_at_t0)
    files = [Path(query_path), Path(response_path), Path(trust_ca_path)]
    if any(not p.is_file() or p.is_symlink() or p.stat().st_size == 0 for p in files):
        raise ExternalProofRefused("trusted CA, original query and signed response required")
    if untrusted_chain_path is not None:
        chain = Path(untrusted_chain_path)
        if not chain.is_file() or chain.is_symlink():
            raise ExternalProofRefused("invalid signer intermediate certificate chain")
    def checked(args, *, stdin=None):
        try:
            proc = runner(args, input=stdin, capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExternalProofRefused("RFC3161 verifier unavailable") from exc
        if proc.returncode != 0:
            # stdout/stderr from openssl might contain local path or CA info.
            raise ExternalProofRefused("RFC3161 signature/nonce/imprint verification failed")
        return proc.stdout.decode("utf8", errors="replace")
    original_query = checked([openssl, "ts", "-query", "-in",
                              str(query_path), "-text"])
    if not re.search(r"^Nonce:\s*0x[0-9a-fA-F]+\s*$", original_query, re.MULTILINE):
        raise ExternalProofRefused("original RFC3161 query lacks random nonce")
    extra = ["-untrusted", str(untrusted_chain_path)] if untrusted_chain_path else []
    base = [openssl, "ts", "-verify", "-in", str(response_path), "-CAfile",
            str(trust_ca_path), *extra]
    checked([*base, "-queryfile", str(query_path)])
    # A client-chosen TSQ with nonce is NOT trusted to commit to these bytes
    # until an entirely separate data-imprint check also passes.
    checked([*base, "-digest", sha256(commitment).hexdigest()])
    details = checked([openssl, "ts", "-reply", "-in", str(response_path), "-text"])
    found = GEN_TIME.search(details)
    if not found:
        raise ExternalProofRefused("signed TSA generation time unavailable")
    try:
        gen_time = datetime.strptime(
            " ".join(found.group(1).split()), "%b %d %H:%M:%S %Y GMT"
        ).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ExternalProofRefused("invalid signed TSA generation time") from exc
    # External existence claim cannot retroactively justify a forecast
    # unboundedly later than purported T0, nor establish exact issue T0.
    if not (issue.replace(microsecond=0) <= gen_time <= issue + timedelta(seconds=300)):
        raise ExternalProofRefused("TSA time outside prospective issuance anchor window")
    return {
        "schema": "G2_EXTERNAL_TIME_ANCHOR_CHECK_V1",
        "status": "SIGNATURE_NONCE_AND_IMPRINT_VERIFIED",
        "tsa_signed_time_utc": gen_time.isoformat().replace("+00:00","Z"),
        "commitment_sha256": sha256(commitment).hexdigest(),
        "independent_provider_data_authenticated": False,
        "independent_exchange_calendar_verified": False,
        "forecast_issued_at_t0_independently_measured": False,
        "before_any_future_outcome_certified": False,
        "production_authorized": False,
    }
