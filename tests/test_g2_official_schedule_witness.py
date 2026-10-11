"""Offline original-byte MOEX schedule revision chain and DuckDB immutability."""
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from birzha.application.prospective_capture import AdmissionRefused, ImmutableCollision
from birzha.application.g2_time_anchor import ExternalProofRefused
from birzha.application.g2_official_schedule_witness import (
    SCHEMA, canonical, digest, verified_schedule_candidate,
    derived_candidate_from_staging,
)
from birzha.storage.g2_schedule_evidence import (
    append_official_schedule_evidence, read_official_schedule_evidence,
)
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
from test_g2_freeze_real_forecast_from_source_receipts import NOW, source_fixture


T0 = "2026-10-10T15:00:20+00:00"
INSTRUMENT = {"secid": "SBER", "engine": "stock", "market": "shares", "board": "TQBR"}


def _document(body: bytes, url: str, observed: str) -> dict:
    return {"url":url,"content_type":"text/html",
            "observed_at_utc":observed, "sha256":digest(body),
            "body_b64":base64.b64encode(body).decode("ascii")}


def witness(two_versions=True):
    first=b"<html>MOEX schedule original synthetic fixture v1. Do NOT treat as real official page</html>"
    second=b"<html>MOEX corrected synthetic session schedule fixture v2</html>"
    docs=[
        _document(first,"https://www.moex.com/n94172",
                  "2026-10-10T12:30:00Z"),
    ]
    first_revision={
        "revision_number":1,"parent_revision_sha256":None,
        "source_document_sha256":digest(first),
        "published_at_utc":"2026-10-09T12:00:00Z",
        "session_identifier":"SBER-TQBR-20261012",
        "session_calendar_date_msk":"2026-10-12",
        "assigned_trading_day_msk":"2026-10-12",
        "session_kind":"REGULAR","instrument_eligible":True,
        "first_future_event_utc":"2026-10-12T03:50:00Z",
        "session_close_utc":"2026-10-12T20:49:59Z",
    }
    versions=[first_revision]
    if two_versions:
        docs.append(_document(
            second,"https://www.moex.com/n96571?nt=106","2026-10-10T14:00:00Z",
        ))
        versions.append({
            **first_revision,
            "revision_number":2,
            "parent_revision_sha256":digest(canonical(first_revision)),
            "source_document_sha256":digest(second),
            "published_at_utc":"2026-10-10T13:00:00Z",
            "session_kind":"SPECIAL_NOTICE",
            "first_future_event_utc":"2026-10-12T06:50:00Z",
            "session_close_utc":"2026-10-12T16:00:00Z",
        })
    return {
        "schema":SCHEMA,
        "forecast_id":"PLACEHOLDER",
        "session_identifier":"SBER-TQBR-20261012",
        "instrument":INSTRUMENT.copy(),
        "source_documents":docs,
        "revisions":versions,
    }


def original_bytes(obj):
    return canonical(obj)


def _draft(obj=None):
    if obj is None:
        obj=witness()
    result=verified_schedule_candidate(
        original_bytes(obj), forecast_id=obj["forecast_id"],
        instrument=INSTRUMENT,forecast_t0=T0,
    )
    assert not result["source_identity_cryptographically_authenticated"]
    assert not result["exchange_calendar_release_approved"]
    return result


def _staging(tmp_path):
    source=tmp_path/"MOEX-synthetic"
    root=tmp_path/"disposable"
    source_fixture(source)
    root.mkdir()
    made=issue_atomic_staging(source,root,"SBER",clock=lambda:NOW)
    journal=SingleDuckDBProspectiveStagingJournal(
        root/"SBER.duckdb",staging_root=root,clock=lambda:NOW
    )
    return journal,root,made["forecast_id"]


def test_two_official_document_versions_are_replayed_by_immutable_hash_chain():
    event=witness()
    result=_draft(event)
    assert result["session_identifier"]=="SBER-TQBR-20261012"
    assert result["earliest_event_utc"]=="2026-10-12T06:50:00Z"
    assert result["effective_revision_sha256"]==digest(canonical(event["revisions"][-1]))
    assert result["original_source_bytes_archived"] is True
    assert result["session_event_time_independently_certified"] is False


@pytest.mark.parametrize("mode",[
    "forged_hostname","http","embedded_credentials","fragment",
    "fake_body_sha","missing_body","after_t0","untrusted_type",
    "broken_parent","missing_revision","wrong_event_sec_id",
    "wrong_board","revised_after_t0","published_after_observed",
    "cancelled_session","session_started_before_t0",
    "wrong_session_day","same_revision_number","reordered_publications",
    "unbound_document","wrong_forecast_id","not_canonical",
])
def test_invalid_revised_schedule_cannot_be_used_as_candidate(mode):
    record=witness()
    if mode=="forged_hostname":
        record["source_documents"][0]["url"]="https://moex.com.evil.invalid/news"
    elif mode=="http":
        record["source_documents"][0]["url"]="http://www.moex.com/n94172"
    elif mode=="embedded_credentials":
        record["source_documents"][0]["url"]="https://user:pass@www.moex.com/n94172"
    elif mode=="fragment":
        record["source_documents"][0]["url"]="https://www.moex.com/n94172#hidden"
    elif mode=="fake_body_sha":
        record["source_documents"][0]["sha256"]="0"*64
    elif mode=="missing_body":
        record["source_documents"][0]["body_b64"]=""
    elif mode=="after_t0":
        record["source_documents"][1]["observed_at_utc"]="2026-10-10T15:01:00Z"
    elif mode=="untrusted_type":
        record["source_documents"][0]["content_type"]="application/octet-stream"
    elif mode=="broken_parent":
        record["revisions"][1]["parent_revision_sha256"]="a"*64
    elif mode=="missing_revision":
        record["revisions"]=[]
    elif mode=="wrong_event_sec_id":
        record["instrument"]["secid"]="GAZP"
    elif mode=="wrong_board":
        record["instrument"]["board"]="OTHER"
    elif mode=="revised_after_t0":
        record["revisions"][1]["published_at_utc"]="2026-10-11T00:00:00Z"
    elif mode=="published_after_observed":
        record["revisions"][0]["published_at_utc"]="2026-10-10T14:00:00Z"
    elif mode=="cancelled_session":
        record["revisions"][1]["session_kind"]="CANCELLED"
    elif mode=="session_started_before_t0":
        record["revisions"][1]["first_future_event_utc"]="2026-10-10T14:00:00Z"
        record["revisions"][1]["session_calendar_date_msk"]="2026-10-10"
    elif mode=="wrong_session_day":
        record["revisions"][1]["session_calendar_date_msk"]="2026-10-15"
    elif mode=="same_revision_number":
        record["revisions"][1]["revision_number"]=1
    elif mode=="reordered_publications":
        record["revisions"][1]["published_at_utc"]="2026-10-09T11:00:00Z"
    elif mode=="unbound_document":
        record["revisions"][1]["source_document_sha256"]="b"*64
    elif mode=="wrong_forecast_id":
        record["forecast_id"]="different-id"
    elif mode=="not_canonical":
        with pytest.raises(ExternalProofRefused):
            verified_schedule_candidate(
                json.dumps(record).encode(), forecast_id="PLACEHOLDER",
                instrument=INSTRUMENT,forecast_t0=T0,
            )
        return
    with pytest.raises(ExternalProofRefused):
        verified_schedule_candidate(
            original_bytes(record),forecast_id="PLACEHOLDER",
            instrument=INSTRUMENT,forecast_t0=T0,
        )


def test_original_source_bytes_and_revisions_survive_staging_restart(tmp_path):
    db,root,fid=_staging(tmp_path)
    record=witness()
    record["forecast_id"]=fid
    original=original_bytes(record)
    try:
        appended=append_official_schedule_evidence(db,fid,original)
        assert appended["status"]=="APPENDED"
        assert appended["earliest_event_utc"]=="2026-10-12T06:50:00Z"
        assert db.audit()["captures"]==1
        assert read_official_schedule_evidence(db,fid)==original
    finally:
        db.close()
    db=SingleDuckDBProspectiveStagingJournal(
        root/"SBER.duckdb",staging_root=root,clock=lambda:NOW
    )
    try:
        assert db.audit()["captures"]==1
        actual=derived_candidate_from_staging(db,fid)
        assert actual["external_schedule_snapshot_sha256"]==digest(original)
        assert actual["exchange_calendar_release_approved"] is False
        assert read_official_schedule_evidence(db,fid)==original
        assert db.db.execute(
            "SELECT COUNT(*) FROM outcome_records"
        ).fetchone()[0]==0
    finally:
        db.close()


def test_changed_original_schedule_replay_fails_even_if_session_same(tmp_path):
    db,_,fid=_staging(tmp_path)
    record=witness()
    record["forecast_id"]=fid
    try:
        first=append_official_schedule_evidence(db,fid,original_bytes(record))
        second=append_official_schedule_evidence(db,fid,original_bytes(record))
        assert first["status"]=="APPENDED"
        assert second["status"]=="DUPLICATE_IDENTICAL"
        record["source_documents"][0]["url"]="https://www.moex.com/a1684"
        with pytest.raises(ImmutableCollision,match="different official schedule"):
            append_official_schedule_evidence(db,fid,original_bytes(record))
    finally:
        db.close()


def test_caller_modified_schedule_after_issuer_is_not_prospective(tmp_path):
    db,_,fid=_staging(tmp_path)
    record=witness()
    record["forecast_id"]=fid
    record["source_documents"][0]["observed_at_utc"]="2026-10-11T09:00:00Z"
    try:
        with pytest.raises(AdmissionRefused):
            append_official_schedule_evidence(db,fid,original_bytes(record))
        assert db.db.execute(
            "SELECT COUNT(*) FROM g2_atomic_schedule_evidence"
        ).fetchone()[0]==0
    finally:
        db.close()


def test_corrupted_source_after_receipt_hash_recalculated_is_still_detected(tmp_path):
    db,_,fid=_staging(tmp_path)
    record=witness()
    record["forecast_id"]=fid
    try:
        append_official_schedule_evidence(db,fid,original_bytes(record))
        raw=db.db.execute(
            "SELECT schedule_blob FROM g2_atomic_schedule_evidence WHERE forecast_id=?",
            [fid],
        ).fetchone()[0]
        tampered=json.loads(bytes(raw))
        tampered["source_documents"][0]["body_b64"]=base64.b64encode(b"forged").decode()
        changed=canonical(tampered)
        db.db.execute(
            "UPDATE g2_atomic_schedule_evidence SET schedule_blob=?,schedule_sha=? "
            "WHERE forecast_id=?",[changed,digest(changed),fid]
        )
        with pytest.raises(ImmutableCollision,match="official schedule"):
            db.audit()
    finally:
        db.close()


def test_tampered_revision_can_not_be_hidden_by_new_audit_hash(tmp_path):
    db,_,fid=_staging(tmp_path)
    record=witness()
    record["forecast_id"]=fid
    try:
        append_official_schedule_evidence(db,fid,original_bytes(record))
        data=db.db.execute(
            "SELECT schedule_blob FROM g2_atomic_schedule_evidence WHERE forecast_id=?",
            [fid],
        ).fetchone()[0]
        tampered=json.loads(bytes(data))
        tampered["revisions"][0]["first_future_event_utc"]="2026-10-12T07:00:00Z"
        encoded=canonical(tampered)
        db.db.execute(
            "UPDATE g2_atomic_schedule_evidence SET schedule_blob=?,schedule_sha=? "
            "WHERE forecast_id=?", [encoded,digest(encoded),fid],
        )
        with pytest.raises(ImmutableCollision,match="official schedule"):
            db.audit()
    finally:
        db.close()


def test_transaction_rollback_after_schedule_insert_does_not_erase_forecast(tmp_path):
    db,_,fid=_staging(tmp_path)
    record=witness()
    record["forecast_id"]=fid
    def fail(marker):
        if marker=="AFTER_ORIGINAL_SCHEDULE_INSERT":
            raise RuntimeError("intentional abort")
    try:
        with pytest.raises(RuntimeError,match="intentional abort"):
            append_official_schedule_evidence(
                db,fid,original_bytes(record),fault_hook=fail
            )
        assert db.db.execute(
            "SELECT COUNT(*) FROM g2_atomic_schedule_evidence"
        ).fetchone()[0]==0
        assert db.audit()["captures"]==1
        assert db.db.execute(
            "SELECT COUNT(*) FROM forecast_records"
        ).fetchone()[0]==1
    finally:
        db.close()


def test_snapshot_does_not_create_fake_schedule_for_missing_forecast(tmp_path):
    db,_,fid=_staging(tmp_path)
    record=witness()
    record["forecast_id"]="missing-Forecast"
    try:
        with pytest.raises(AdmissionRefused):
            append_official_schedule_evidence(
                db,"missing-Forecast",original_bytes(record)
            )
    finally:
        db.close()


def test_newly_discovered_unreviewed_notice_never_silently_ignored():
    record=witness()
    extra=b"<html>synthetic unreviewed schedule amendment</html>"
    record["source_documents"].append(_document(
        extra,"https://www.moex.com/n99998","2026-10-10T14:05:00Z",
    ))
    with pytest.raises(ExternalProofRefused):
        verified_schedule_candidate(
            original_bytes(record),forecast_id="PLACEHOLDER",
            instrument=INSTRUMENT,forecast_t0=T0,
        )


def test_positive_notice_chain_explicitly_denies_provider_auth_and_completeness():
    record=witness()
    result=verified_schedule_candidate(
        original_bytes(record),forecast_id="PLACEHOLDER",
        instrument=INSTRUMENT,forecast_t0=T0,
    )
    assert result["notice_inventory_completeness_independently_verified"] is False
    assert result["published_at_time_independently_authenticated"] is False
    assert result["source_identity_cryptographically_authenticated"] is False
