"""Two-phase immutable RFC3161 proof retention in the same STAGING DuckDB file.

An already-issued immutable Forecast is NOT rewritten when an external TSA
response arrives. This does not assert that a caller-provided CA was approved.
No network requests, hidden trust anchors or production publishing.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from birzha.application import prospective_capture as pilot
from birzha.application.g2_time_anchor import (
    ExternalProofRefused, original_forecast_commitment, verify_rfc3161,
)
from birzha.storage.g2_manifest_evidence import verified_original_manifest


SCHEMA = "G2_ATOMIC_RFC3161_WITNESS_V1"
MAX_ARTIFACT = 2_000_000


def digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def read_artifact(path: Path, *, optional=False) -> bytes | None:
    if optional and path is None:
        return None
    p = Path(path)
    if not p.is_file() or p.is_symlink() or not 0 < p.stat().st_size <= MAX_ARTIFACT:
        raise ExternalProofRefused("bounded original RFC3161 evidence file required")
    data = p.read_bytes()
    if not 0 < len(data) <= MAX_ARTIFACT:
        raise ExternalProofRefused("changed original RFC3161 evidence file")
    return data


def _content_fingerprint(query, response, ca, chain, commitment):
    return tuple(digest(v) if v is not None else None
                 for v in (query, response, ca, chain, commitment))


def _original_commitment_from_db(db, forecast_id: str):
    row = db.execute(
        "SELECT f.payload_json,c.source_blob,c.receipt_json "
        "FROM forecast_records f JOIN g2_atomic_captures c "
        "ON f.forecast_id=c.forecast_id WHERE f.forecast_id=?",
        [forecast_id],
    ).fetchone()
    if row is None:
        raise ExternalProofRefused("exact immutable Forecast not found")
    payload, raw, receipt = row
    manifest = verified_original_manifest(json.loads(receipt), bytes(raw))
    if manifest is None:
        raise ExternalProofRefused("legacy Forecast has no original page receipts")
    forecast = json.loads(payload)
    return (
        original_forecast_commitment(payload.encode("utf8"),bytes(raw),manifest),
        forecast["created_at_t0"],
    )


def audit_time_anchor_rows(db):
    """Byte integrity + exact Forecast binding on every ordinary journal audit.

    Trust-chain revocation is NOT silently rechecked here. Use explicitly
    configured independent CA to reverify saved bytes before relying on proof.
    """
    rows = db.execute(
        "SELECT forecast_id,commitment,commitment_sha,query_blob,query_sha,"
        "response_blob,response_sha,ca_blob,ca_sha,chain_blob,chain_sha,"
        "verification_json,verification_sha FROM g2_atomic_time_anchors"
    ).fetchall()
    for fid,commitment,commitsha,q,qsha,r,rsha,ca,casha,chain,chainsha,statement,statsha in rows:
        try:
            for blob, expected in ((commitment,commitsha),(q,qsha),(r,rsha),(ca,casha)):
                if not isinstance(blob,(bytes,bytearray,memoryview)) or digest(bytes(blob))!=expected:
                    raise ValueError("changed originally attested bytes")
            if (chain is None)!=(chainsha is None):
                raise ValueError("incomplete signer chain")
            if chain is not None and digest(bytes(chain))!=chainsha:
                raise ValueError("signer intermediates changed")
            canonical_commit, claimed_t0 = _original_commitment_from_db(db,fid)
            if bytes(commitment)!=canonical_commit:
                raise ValueError("detached original Forecast commitment")
            if digest(statement.encode("utf8"))!=statsha:
                raise ValueError("verification receipt changed")
            evidence=json.loads(statement)
            if (evidence.get("schema")!=SCHEMA
                or evidence.get("forecast_id")!=fid
                or evidence.get("commitment_sha256")!=commitsha
                or evidence.get("original_query_sha256")!=qsha
                or evidence.get("signed_response_sha256")!=rsha
                or evidence.get("caller_supplied_ca_sha256")!=casha
                or evidence.get("untrusted_chain_sha256")!=chainsha
                or evidence.get("forecast_claimed_t0")!=claimed_t0
                or evidence.get("external_ca_independently_approved") is not False
                or evidence.get("production_authorized") is not False):
                raise ValueError("timestamp evidence contract changed")
        except (ValueError,KeyError,TypeError,UnicodeError) as exc:
            raise pilot.ImmutableCollision("immutable RFC3161 evidence corrupted") from exc
    return len(rows)


def append_signed_time_evidence(store, forecast_id: str, *,
                                query_path: Path, response_path: Path,
                                ca_path: Path, untrusted_chain_path: Path | None = None,
                                fault_hook=None, runner=None):
    """Verify frozen snapshots cryptographically, then atomically append proof.

    A self-signed or privately controlled CA is *not* independent trust; the
    caller-supplied CA is retained with a SHA but never approved by this code.
    """
    from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
    if not isinstance(store, SingleDuckDBProspectiveStagingJournal):
        raise ExternalProofRefused("disposable canonical single-DuckDB staging required")
    query = read_artifact(query_path)
    response = read_artifact(response_path)
    ca = read_artifact(ca_path)
    chain = read_artifact(untrusted_chain_path,optional=True)
    with store.lock:
        store._audit()
        commitment, t0 = _original_commitment_from_db(store.db,forecast_id)
        # The verifier always receives exactly these frozen bytes, never
        # mutable externally supplied paths that might change between checks.
        with TemporaryDirectory(prefix="g2-tsa-snapshot-") as tmp:
            root=Path(tmp)
            for name,body in (("query.tsq",query),("reply.tsr",response),
                              ("ca.pem",ca),("chain.pem",chain)):
                if body is not None:
                    dst=root/name
                    dst.write_bytes(body)
                    dst.chmod(0o600)
            kw = {"runner":runner} if runner is not None else {}
            verified=verify_rfc3161(
                commitment,issued_at_t0=t0,
                query_path=root/"query.tsq",response_path=root/"reply.tsr",
                trust_ca_path=root/"ca.pem",
                untrusted_chain_path=(root/"chain.pem" if chain is not None else None),
                **kw,
            )
        evidence={
            "schema":SCHEMA,
            "forecast_id":forecast_id,
            "forecast_claimed_t0":t0,
            "commitment_sha256":digest(commitment),
            "original_query_sha256":digest(query),
            "signed_response_sha256":digest(response),
            "caller_supplied_ca_sha256":digest(ca),
            "untrusted_chain_sha256":digest(chain) if chain is not None else None,
            "local_crypto_verification":verified["status"],
            "tsa_signed_time_utc":verified["tsa_signed_time_utc"],
            "external_ca_independently_approved":False,
            "forecast_t0_independently_measured":False,
            "independent_provider_and_calendar_attested":False,
            "before_future_outcome_cutoff_certified":False,
            "production_authorized":False,
        }
        json_body=canonical(evidence)
        values=(forecast_id,commitment,digest(commitment),query,digest(query),
                response,digest(response),ca,digest(ca),chain,
                digest(chain) if chain is not None else None,
                json_body,digest(json_body.encode("utf8")))
        def operation():
            # Re-derive under transaction, to refuse stale/colliding forecast.
            frozen,_=_original_commitment_from_db(store.db,forecast_id)
            if frozen!=commitment:
                raise pilot.ImmutableCollision("Forecast changed after TSA verification")
            previous=store.db.execute(
                "SELECT commitment_sha,query_sha,response_sha,ca_sha,chain_sha "
                "FROM g2_atomic_time_anchors WHERE forecast_id=?",[forecast_id],
            ).fetchone()
            if previous is not None:
                if previous != (values[2],values[4],values[6],values[8],values[10]):
                    raise pilot.ImmutableCollision("different RFC3161 evidence on replay")
                return {"status":"DUPLICATE_IDENTICAL",**evidence}
            store.db.execute(
                "INSERT INTO g2_atomic_time_anchors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                list(values),
            )
            if fault_hook is not None:
                fault_hook("AFTER_EXTERNAL_TIMESTAMP_EVIDENCE")
            return {"status":"APPENDED",**evidence}
        return store._tx(operation)


def read_signed_time_evidence(store, forecast_id: str) -> dict | None:
    from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
    if not isinstance(store,SingleDuckDBProspectiveStagingJournal):
        raise ExternalProofRefused("single-DuckDB staging required")
    with store.lock:
        store._audit()
        row=store.db.execute(
            "SELECT commitment,query_blob,response_blob,ca_blob,chain_blob,verification_json "
            "FROM g2_atomic_time_anchors WHERE forecast_id=?",[forecast_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "original_commitment":bytes(row[0]),
            "original_query":bytes(row[1]),
            "signed_response":bytes(row[2]),
            "caller_supplied_ca":bytes(row[3]),
            "untrusted_chain":bytes(row[4]) if row[4] is not None else None,
            "verification":json.loads(row[5]),
        }
