"""Append-only original MOEX official schedule snapshots in ONE G2 staging DuckDB.

This stores original retained document bytes and their untrusted local receipt
claims. It never grants independent calendar authenticity or release approval.
"""
from __future__ import annotations

from hashlib import sha256
import json

from birzha.application import prospective_capture as pilot
from birzha.application.g2_official_schedule_witness import (
    canonical, digest, verified_schedule_candidate,
)
from birzha.storage.g2_manifest_evidence import verified_original_manifest


def _canonical_inputs(db, fid: str):
    row = db.execute(
        "SELECT f.payload_json,c.source_blob,c.receipt_json "
        "FROM forecast_records f JOIN g2_atomic_captures c "
        "ON f.forecast_id=c.forecast_id WHERE f.forecast_id=?",
        [fid],
    ).fetchone()
    if row is None:
        raise ValueError("immutable Forecast receipt missing")
    forecast=json.loads(row[0])
    if forecast.get("forecast_id")!=fid:
        raise ValueError("Forecast identity differs")
    manifest_blob=verified_original_manifest(json.loads(row[2]),bytes(row[1]))
    if manifest_blob is None:
        raise ValueError("original source manifest missing")
    manifest=json.loads(manifest_blob)
    matches=[x for x in manifest.get("instruments",[]) if isinstance(x,dict)
             and x.get("resolved_secid")==forecast.get("secid")
             and x.get("status")=="SOURCE_CAPTURE_COMPLETE"]
    if len(matches)!=1 or not isinstance(matches[0].get("instrument"),dict):
        raise ValueError("exact SECID source route missing or ambiguous")
    return matches[0]["instrument"],forecast["created_at_t0"]


def audit_official_schedule_rows(db):
    rows=db.execute(
        "SELECT forecast_id,schedule_blob,schedule_sha,candidate_json,candidate_sha "
        "FROM g2_atomic_schedule_evidence"
    ).fetchall()
    for fid,raw,hashed,statement,statement_hash in rows:
        try:
            if digest(bytes(raw))!=hashed or digest(statement.encode("utf8"))!=statement_hash:
                raise ValueError("tampered schedule envelope/candidate")
            instrument,t0=_canonical_inputs(db,fid)
            expected=verified_schedule_candidate(
                bytes(raw),forecast_id=fid,instrument=instrument,forecast_t0=t0,
            )
            if canonical(expected).decode("utf8").strip()!=statement:
                raise ValueError("candidate changed since schedule verification")
            if (expected.get("exchange_calendar_release_approved") is not False
                or expected.get("source_identity_cryptographically_authenticated") is not False
                or expected.get("session_event_time_independently_certified") is not False):
                raise ValueError("schedule review promoted an unauthenticated claim")
        except (ValueError,TypeError,KeyError,UnicodeError) as exc:
            raise pilot.ImmutableCollision("original official schedule evidence corrupt") from exc
    return len(rows)


def append_official_schedule_evidence(store, forecast_id: str, schedule_bytes: bytes,
                                      *, fault_hook=None) -> dict:
    from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
    if not isinstance(store, SingleDuckDBProspectiveStagingJournal):
        raise ValueError("disposable canonical single-DuckDB writer required")
    with store.lock:
        store._audit()
        try:
            instrument,t0=_canonical_inputs(store.db,forecast_id)
            candidate=verified_schedule_candidate(
                schedule_bytes,forecast_id=forecast_id,
                instrument=instrument,forecast_t0=t0,
            )
        except (ValueError,TypeError,KeyError) as exc:
            raise pilot.AdmissionRefused("unverified versioned original schedule witness") from exc
        evidence=canonical(candidate).decode("utf8").strip()
        values=(forecast_id,bytes(schedule_bytes),digest(schedule_bytes),
                evidence,digest(evidence.encode("utf8")))
        def save():
            stored=store.db.execute(
                "SELECT schedule_sha FROM g2_atomic_schedule_evidence WHERE forecast_id=?",
                [forecast_id],
            ).fetchone()
            if stored is not None:
                if stored[0]!=values[2]:
                    raise pilot.ImmutableCollision("different official schedule revision replay")
                return {"status":"DUPLICATE_IDENTICAL",**candidate}
            store.db.execute(
                "INSERT INTO g2_atomic_schedule_evidence VALUES (?,?,?,?,?)",
                list(values),
            )
            if fault_hook is not None:
                fault_hook("AFTER_ORIGINAL_SCHEDULE_INSERT")
            return {"status":"APPENDED",**candidate}
        return store._tx(save)


def read_official_schedule_evidence(store, forecast_id: str) -> bytes | None:
    from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
    if not isinstance(store, SingleDuckDBProspectiveStagingJournal):
        raise ValueError("canonical single-DuckDB staging required")
    with store.lock:
        store._audit()
        row=store.db.execute(
            "SELECT schedule_blob FROM g2_atomic_schedule_evidence WHERE forecast_id=?",
            [forecast_id],
        ).fetchone()
        return bytes(row[0]) if row else None
