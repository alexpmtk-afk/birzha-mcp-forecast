"""Append-only RFC3161 evidence persistence: OFFLINE mock signatures, real DuckDB."""
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from birzha.application.prospective_capture import ImmutableCollision
from birzha.application.g2_time_anchor import ExternalProofRefused
from birzha.storage.g2_timestamp_evidence import (
    append_signed_time_evidence,read_signed_time_evidence,
)
from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
from test_g2_freeze_real_forecast_from_source_receipts import NOW,source_fixture


class FakeProc:
    def __init__(self, data=b"", code=0):
        self.returncode,self.stdout,self.stderr=code,data,b""


def synthetic_openssl(args, input=None, **kwargs):
    if "-query" in args:
        return FakeProc(b"Nonce: 0xFEED1234\n")
    if "-queryfile" in args or "-digest" in args:
        return FakeProc()
    if "-reply" in args and "-text" in args:
        return FakeProc(b"Time stamp: Oct 10 15:00:30 2026 GMT\n")
    return FakeProc(code=1)


def materials(tmp_path):
    evidence=tmp_path/"ext"
    evidence.mkdir()
    for name,raw in [
        ("original.tsq",b"SYNTHETIC-nonce-bearing-query"),
        ("signed.tsr",b"SYNTHETIC-der-not-real"),
        ("ca.pem",b"SYNTHETIC-CA-NOT-TRUSTED"),
    ]:
        (evidence/name).write_bytes(raw)
    return dict(query_path=evidence/"original.tsq",
                response_path=evidence/"signed.tsr",ca_path=evidence/"ca.pem")


def staging(tmp_path):
    src=tmp_path/"original"
    root=tmp_path/"disposable"
    source_fixture(src)
    root.mkdir()
    saved=issue_atomic_staging(src,root,"SBER",clock=lambda:NOW)
    store=SingleDuckDBProspectiveStagingJournal(
        root/"SBER.duckdb",staging_root=root,clock=lambda:NOW,
    )
    return store,root,saved["forecast_id"]


def test_separate_signed_artifacts_survive_restart_and_cannot_auto_grant_trust(tmp_path):
    journal,root,fid=staging(tmp_path)
    original=materials(tmp_path)
    try:
        result=append_signed_time_evidence(
            journal,fid,runner=synthetic_openssl,**original
        )
        assert result["status"]=="APPENDED"
        assert result["external_ca_independently_approved"] is False
        assert result["production_authorized"] is False
        assert result["forecast_t0_independently_measured"] is False
        assert journal.audit()["captures"]==1
        assert journal.db.execute("SELECT COUNT(*) FROM forecast_records").fetchone()[0]==1
    finally:
        journal.close()
    journal=SingleDuckDBProspectiveStagingJournal(
        root/"SBER.duckdb",staging_root=root,clock=lambda:NOW
    )
    try:
        assert journal.audit()["captures"]==1
        retrieved=read_signed_time_evidence(journal,fid)
        assert retrieved["original_query"]==original["query_path"].read_bytes()
        assert retrieved["signed_response"]==original["response_path"].read_bytes()
        assert retrieved["caller_supplied_ca"]==original["ca_path"].read_bytes()
        assert retrieved["verification"]["external_ca_independently_approved"] is False
        assert retrieved["original_commitment"].startswith(b"{")
        assert len(list(root.glob("*.duckdb")))==1
    finally:
        journal.close()


def test_identical_timestamp_replay_is_idempotent_different_token_refused(tmp_path):
    journal,root,fid=staging(tmp_path)
    files=materials(tmp_path)
    try:
        assert append_signed_time_evidence(
            journal,fid,runner=synthetic_openssl,**files
        )["status"]=="APPENDED"
        assert append_signed_time_evidence(
            journal,fid,runner=synthetic_openssl,**files
        )["status"]=="DUPLICATE_IDENTICAL"
        files["response_path"].write_bytes(b"ANOTHER-PRETENDED-TSA-TOKEN")
        with pytest.raises(ImmutableCollision,match="different RFC3161"):
            append_signed_time_evidence(
                journal,fid,runner=synthetic_openssl,**files
            )
        assert journal.audit()["captures"]==1
    finally:
        journal.close()


@pytest.mark.parametrize("field",["original_query_sha256","signed_response_sha256","caller_supplied_ca_sha256"])
def test_rehashed_time_verification_receipt_still_fails_blob_binding(tmp_path,field):
    journal,_,fid=staging(tmp_path)
    files=materials(tmp_path)
    try:
        append_signed_time_evidence(journal,fid,runner=synthetic_openssl,**files)
        row=journal.db.execute(
            "SELECT verification_json FROM g2_atomic_time_anchors WHERE forecast_id=?",
            [fid],
        ).fetchone()[0]
        altered=json.loads(row)
        altered[field]="0"*64
        body=json.dumps(altered,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        journal.db.execute(
            "UPDATE g2_atomic_time_anchors "
            "SET verification_json=?,verification_sha=? WHERE forecast_id=?",
            [body,sha256(body.encode()).hexdigest(),fid],
        )
        with pytest.raises(ImmutableCollision,match="RFC3161"):
            journal.audit()
    finally:
        journal.close()


def test_failed_signature_does_not_append_untrusted_timestamp(tmp_path):
    journal,_,fid=staging(tmp_path)
    files=materials(tmp_path)
    def denied(args, input=None, **kwargs):
        if "-query" in args:
            return FakeProc(b"Nonce: 0x1234\n")
        return FakeProc(code=1)
    try:
        with pytest.raises(ExternalProofRefused,match="signature"):
            append_signed_time_evidence(journal,fid,runner=denied,**files)
        assert journal.db.execute(
            "SELECT COUNT(*) FROM g2_atomic_time_anchors"
        ).fetchone()[0]==0
    finally:
        journal.close()


def test_missing_forecast_no_external_timestamp_or_generated_forecast(tmp_path):
    journal,_,fid=staging(tmp_path)
    files=materials(tmp_path)
    try:
        with pytest.raises(ExternalProofRefused,match="not found"):
            append_signed_time_evidence(
                journal,"not-a-Forecast",runner=synthetic_openssl,**files
            )
        assert journal.db.execute(
            "SELECT COUNT(*) FROM g2_atomic_time_anchors"
        ).fetchone()[0]==0
    finally:
        journal.close()


def test_failure_after_storing_timestamp_evidence_rolls_back_only_anchor(tmp_path):
    journal,_,fid=staging(tmp_path)
    files=materials(tmp_path)
    def kill(event):
        if event=="AFTER_EXTERNAL_TIMESTAMP_EVIDENCE":
            raise OSError("synthetic interrupted writer")
    try:
        with pytest.raises(OSError,match="synthetic interrupted"):
            append_signed_time_evidence(
                journal,fid,runner=synthetic_openssl,fault_hook=kill,**files
            )
        assert journal.db.execute(
            "SELECT COUNT(*) FROM g2_atomic_time_anchors"
        ).fetchone()[0]==0
        assert journal.db.execute(
            "SELECT COUNT(*) FROM forecast_records"
        ).fetchone()[0]==1
        assert journal.audit()["captures"]==1
    finally:
        journal.close()


def test_nonce_missing_must_fail_even_if_crypto_mock_says_valid(tmp_path):
    journal,_,fid=staging(tmp_path)
    files=materials(tmp_path)
    def missing_nonce(args, input=None, **kwargs):
        if "-query" in args:
            return FakeProc(b"Nonce: unspecified\n")
        return synthetic_openssl(args,input=input,**kwargs)
    try:
        with pytest.raises(ExternalProofRefused,match="lacks random nonce"):
            append_signed_time_evidence(
                journal,fid,runner=missing_nonce,**files
            )
        assert read_signed_time_evidence(journal,fid) is None
    finally:
        journal.close()


def test_forged_release_flag_is_rejected_even_after_receipt_sha_recalculated(tmp_path):
    journal,_,fid=staging(tmp_path)
    files=materials(tmp_path)
    try:
        append_signed_time_evidence(journal,fid,runner=synthetic_openssl,**files)
        old=journal.db.execute(
            "SELECT verification_json FROM g2_atomic_time_anchors WHERE forecast_id=?",
            [fid],
        ).fetchone()[0]
        receipt=json.loads(old)
        receipt["forecast_t0_independently_measured"]=True
        receipt["before_future_outcome_cutoff_certified"]=True
        forged=json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        journal.db.execute(
            "UPDATE g2_atomic_time_anchors SET verification_json=?,verification_sha=? "
            "WHERE forecast_id=?",
            [forged,sha256(forged.encode()).hexdigest(),fid],
        )
        with pytest.raises(ImmutableCollision,match="RFC3161"):
            journal.audit()
    finally:
        journal.close()
