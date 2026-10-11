"""Offline candidate TSA trust/SECID future cutoff review. No live TSA or MOEX."""
from datetime import datetime, timezone
from hashlib import sha256
import json

import pytest

from birzha.application.g2_time_anchor import ExternalProofRefused
from birzha.application.g2_tsa_policy_cutoff_review import (
    CUTOFF_SCHEMA, REVIEW_SCHEMA, _accuracy_ms, conditional_before_event_review,
)
from birzha.storage.g2_timestamp_evidence import append_signed_time_evidence
from test_g2_rfc3161_timestamp_retention import staging, materials, FakeProc


GEN_TIME = "Time stamp: Oct 10 15:00:30 2026 GMT\n"


def synthetically_signed(args, input=None, **kwargs):
    if "-query" in args:
        return FakeProc(b"Nonce: 0xABCD1234\n")
    if "-queryfile" in args or "-digest" in args:
        return FakeProc()
    if "-reply" in args and "-text" in args:
        return FakeProc((
            "TST info:\nPolicy OID: 1.2.3.4.5\n" + GEN_TIME +
            "Accuracy: 0x01 seconds, 0x00 millis, 0x00 micros\n"
        ).encode())
    return FakeProc(code=1)


def _policies(files):
    profile={
        "schema":REVIEW_SCHEMA,
        "reviewed_ca_bundle_sha256":sha256(files["ca_path"].read_bytes()).hexdigest(),
        "acceptable_tsa_policy_oids":["1.2.3.4.5"],
        "max_tsa_accuracy_ms":2_000,
    }
    event={
        "schema":CUTOFF_SCHEMA,
        "secid":"SBER",
        "engine":"stock",
        "market":"shares",
        "board":"TQBR",
        "session_identifier":"SBER-TQBR-FUTURE-SESSION-CANDIDATE-01",
        "cutoff_kind":"FIRST_FUTURE_MARKET_EVENT",
        "earliest_event_utc":"2026-10-12T09:50:00Z",
        "external_schedule_snapshot_sha256":"a"*64,
    }
    return profile,event


def _archived(tmp_path):
    journal,root,fid=staging(tmp_path)
    artifacts=materials(tmp_path)
    outcome=append_signed_time_evidence(
        journal,fid,runner=synthetically_signed,**artifacts
    )
    assert outcome["status"]=="APPENDED"
    return journal,fid,artifacts


def test_conditional_ordering_never_approves_external_ca_or_production(tmp_path):
    journal,fid,files=_archived(tmp_path)
    try:
        profile,event=_policies(files)
        result=conditional_before_event_review(
            journal,fid,trust_candidate=profile,
            event_cutoff_candidate=event,runner=synthetically_signed,
        )
        assert result["status"]=="CANDIDATE_CRYPTO_AND_TIME_ORDER_CONSISTENT"
        assert result["tsa_accuracy_upper_bound_ms"]==1000
        assert result["tsa_policy_oid"]=="1.2.3.4.5"
        assert result["tsa_signed_time_utc"]=="2026-10-10T15:00:30Z"
        assert result["independent_tsa_authority_approved"] is False
        assert result["exchange_session_cutoff_independently_certified"] is False
        assert result["production_authorized"] is False
        assert result["release_gate_can_close"] is False
    finally:
        journal.close()


@pytest.mark.parametrize("mutation",[
    "ca", "policy", "inaccurate", "missing_accuracy",
    "wrong_secid", "wrong_board", "no_schedule_hash",
    "unbounded_deadline", "missing_timezone", "overlap",
    "wrong_cutoff_kind", "wrong_session_id", "wrong_evidence_schema",
    "missing_history_of_timestamp",
])
def test_tsa_policy_or_event_evidence_not_sufficient_fails_closed(tmp_path,mutation):
    journal,fid,files=_archived(tmp_path)
    try:
        profile,event=_policies(files)
        runner=synthetically_signed
        if mutation=="ca":
            profile["reviewed_ca_bundle_sha256"]="0"*64
        elif mutation=="policy":
            profile["acceptable_tsa_policy_oids"]=["1.2.3.4.99"]
        elif mutation=="inaccurate":
            profile["max_tsa_accuracy_ms"]=500
        elif mutation=="missing_accuracy":
            def runner(args, input=None, **kwargs):
                if "-reply" in args and "-text" in args:
                    return FakeProc(("Policy OID: 1.2.3.4.5\n"+GEN_TIME).encode())
                return synthetically_signed(args,input=input,**kwargs)
        elif mutation=="wrong_secid":
            event["secid"]="GAZP"
        elif mutation=="wrong_board":
            event["board"]="OTHER"
        elif mutation=="no_schedule_hash":
            event["external_schedule_snapshot_sha256"]=None
        elif mutation=="unbounded_deadline":
            event["earliest_event_utc"]="2026-10-10T15:00:29Z"
        elif mutation=="missing_timezone":
            event["earliest_event_utc"]="2026-10-12T09:50:00"
        elif mutation=="overlap":
            event["earliest_event_utc"]="2026-10-10T15:00:30.500Z"
        elif mutation=="wrong_cutoff_kind":
            event["cutoff_kind"]="LAST_COMPLETED_TRADING_DAY"
        elif mutation=="wrong_session_id":
            event["session_identifier"]=""
        elif mutation=="wrong_evidence_schema":
            profile["schema"]="UNAUTHENTICATED"
        elif mutation=="missing_history_of_timestamp":
            journal.db.execute("DELETE FROM g2_atomic_time_anchors WHERE forecast_id=?",[fid])
        with pytest.raises(ExternalProofRefused):
            conditional_before_event_review(
                journal,fid,trust_candidate=profile,
                event_cutoff_candidate=event,runner=runner,
            )
    finally:
        journal.close()


@pytest.mark.parametrize("text,expected",[
    ("Accuracy: 0x01 seconds, 0x01 millis, 0x01 micros\n",1002),
    ("Accuracy: 2 seconds, 5 millis\n",2005),
    ("Accuracy: 0x00 seconds, 0x01 micros\n",1),
])
def test_tsa_accuracy_rounds_up_never_underestimates(text,expected):
    assert _accuracy_ms(text)==expected


@pytest.mark.parametrize("text",[
    "", "Accuracy: 0 seconds, 0 millis\n", "Accuracy: unknown\n",
    "Accuracy: 0 seconds, 1000 millis\n",
    "Accuracy: 1 seconds, 1 seconds\n",
])
def test_absent_malformed_or_zero_uncertainty_never_certified(text):
    with pytest.raises(ExternalProofRefused):
        _accuracy_ms(text)
