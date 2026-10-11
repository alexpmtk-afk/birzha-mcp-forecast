"""Offline verified-source and cryptographic attestation admission semantics.

No real MOEX, TSA, HOME or production requests; mocked OpenSSL is explicitly
synthetic and can never serve as a real independent time proof.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import subprocess

import pytest

from birzha.application.g2_calendar_witness import (
    CalendarWitnessRefused, SCHEMA, verify_exact_history_concordance,
)
from birzha.application.g2_time_anchor import (
    ExternalProofRefused, original_forecast_commitment, verify_rfc3161,
)


ISSUED = "2026-10-11T09:27:25Z"


def _body(day, trades, end=""):
    return json.dumps({
        "candles": {
            "columns": ["begin", "end", "close"],
            "data": [[f"{day} 10:00:00", end or f"{day} 23:49:59", 100]],
        }
    }).encode()


def _history(day, trades=10):
    return json.dumps({
        "history": {
            "columns": ["TRADEDATE","NUMTRADES","VOLUME","VALUE"],
            "data": [[day, trades, 200 if trades else 0, 3000 if trades else 0]],
        },
        "history.cursor": {
            "columns": ["TOTAL", "PAGESIZE"],
            "data": [[1, 100]],
        },
    }).encode()


def _witness(day="2026-10-09", trades=10):
    original = _history(day,trades)
    return {
        "schema": SCHEMA, "engine": "stock", "market": "shares",
        "board": "TQBR", "secid": "SBER",
        "path": "/history/engines/stock/markets/shares/boards/TQBR/securities/SBER.json",
        "requested_from": "2026-10-05", "requested_till": "2026-10-10",
        "pages": [{
            "start": 0, "raw_body": original, "sha256": sha256(original).hexdigest(),
            "observed_start_utc": "2026-10-11T09:27:15Z",
            "observed_end_utc": "2026-10-11T09:27:20Z",
        }]
    }


def _route():
    return {"engine": "stock","market":"shares","board":"TQBR","secid":"SBER"}


def test_same_exchange_independent_endpoint_agrees_exact_secid_and_latest_session():
    proof = verify_exact_history_concordance(
        instrument=_route(), d1_raw_pages=(_body("2026-10-09", 10),),
        witness=_witness(), issue_t0=ISSUED
    )
    assert proof["status"] == "MOEX_SECOND_ENDPOINT_ACTIVITY_CONCORDANCE"
    assert proof["last_active_history_session"] == "2026-10-09"
    assert proof["matched_recent_sessions"] == 1
    assert proof["exchange_schedule_independently_certified"] is False
    assert proof["source_time_independently_attested"] is False


@pytest.mark.parametrize("error", [
    "wrong_secid", "wrong_board", "mutated_sha", "missing_activity",
    "wrong_session", "stale_receipt", "wrong_path", "missing_page",
])
def test_calendar_witness_deny_on_missing_untrusted_contradictory_history(error):
    witness = _witness()
    if error == "wrong_secid":
        witness["secid"] = "GAZP"
    elif error == "wrong_board":
        witness["board"] = "FQBR"
    elif error == "mutated_sha":
        witness["pages"][0]["sha256"] = "0"*64
    elif error == "missing_activity":
        raw = json.dumps({"history":{"columns":["TRADEDATE"], "data":[["2026-10-09"]]}}).encode()
        witness["pages"][0]["raw_body"] = raw
        witness["pages"][0]["sha256"] = sha256(raw).hexdigest()
    elif error == "wrong_session":
        witness = _witness("2026-10-08")
    elif error == "stale_receipt":
        witness["pages"][0]["observed_end_utc"] = "2026-10-01T00:00:00Z"
    elif error == "wrong_path":
        witness["path"] = witness["path"].replace("SBER","GAZP")
    elif error == "missing_page":
        witness["pages"] = []
    with pytest.raises(CalendarWitnessRefused):
        verify_exact_history_concordance(
            instrument=_route(), d1_raw_pages=(_body("2026-10-09",10),),
            witness=witness, issue_t0=ISSUED
        )


def test_zero_activity_placeholder_is_not_considered_a_trading_session():
    witness = _witness(trades=0)
    with pytest.raises(CalendarWitnessRefused):
        verify_exact_history_concordance(
            instrument=_route(), d1_raw_pages=(_body("2026-10-09",10),),
            witness=witness, issue_t0=ISSUED
        )


def _original_statement():
    forecast = json.dumps({
        "forecast_id":"real-staging-example-001", "secid":"SBER",
        "created_at_t0":ISSUED,
    }, sort_keys=True).encode()
    raw = b"synthetic complete source blob"
    manifest = json.dumps({
        "schema":"G2_SIX_MARKET_LIVE_SOURCE_RECEIPT_V1",
        "instruments":[{"resolved_secid":"SBER", "status":"SOURCE_CAPTURE_COMPLETE"}],
    }).encode()
    return original_forecast_commitment(forecast, raw, manifest), forecast, raw, manifest


def test_external_timestamp_statement_binds_all_three_immutable_inputs():
    body, forecast, source, manifest = _original_statement()
    data = json.loads(body)
    assert data["forecast_payload_sha256"] == sha256(forecast).hexdigest()
    assert data["original_source_blob_sha256"] == sha256(source).hexdigest()
    assert data["original_source_manifest_sha256"] == sha256(manifest).hexdigest()
    assert data["external_time_attestation"] == "REQUIRES_RFC3161_SIGNED_RESPONSE"
    assert body == original_forecast_commitment(forecast, source, manifest)
    assert body != original_forecast_commitment(forecast, source+b"x", manifest)


def test_external_timestamp_missing_real_signed_token_never_passes(tmp_path):
    statement,*_ = _original_statement()
    with pytest.raises(ExternalProofRefused, match="trusted CA"):
        verify_rfc3161(
            statement, issued_at_t0=ISSUED, query_path=tmp_path/"missing.tsq",
            response_path=tmp_path/"missing.tsr", trust_ca_path=tmp_path/"missing.crt",
        )


class _Proc:
    def __init__(self, text=b"", code=0):
        self.stdout, self.stderr, self.returncode = text,b"",code


def test_rfc3161_verifier_requires_nonce_and_original_data_imprint(tmp_path):
    statement,*_ = _original_statement()
    for name in ("q.tsq","r.tsr","external-ca.pem"):
        (tmp_path/name).write_bytes(b"synthetic test data, NOT a real signature")
    calls = []
    def fake(args, input=None, **kwargs):
        calls.append((args,input))
        if "-text" in args:
            return _Proc(b"TST info:\nTime stamp: Oct 11 09:27:30 2026 GMT\n")
        return _Proc()
    outcome = verify_rfc3161(
        statement, issued_at_t0=ISSUED,
        query_path=tmp_path/"q.tsq",
        response_path=tmp_path/"r.tsr",
        trust_ca_path=tmp_path/"external-ca.pem", runner=fake,
    )
    assert outcome["status"] == "SIGNATURE_NONCE_AND_IMPRINT_VERIFIED"
    assert outcome["forecast_issued_at_t0_independently_measured"] is False
    assert outcome["independent_exchange_calendar_verified"] is False
    assert len(calls) == 3
    assert "-queryfile" in calls[0][0] and "-CAfile" in calls[0][0]
    assert "-data" in calls[1][0] and calls[1][1] == statement


@pytest.mark.parametrize("failure", ["nonce", "imprint", "late"])
def test_rfc3161_verify_fails_closed_on_missing_request_proof_or_late_stamp(tmp_path, failure):
    statement,*_ = _original_statement()
    for name in ("q.tsq","r.tsr","external-ca.pem"):
        (tmp_path/name).write_bytes(b"synthetic bytes")
    def fake(args, input=None, **kwargs):
        if "-queryfile" in args and failure == "nonce":
            return _Proc(code=1)
        if "-data" in args and failure == "imprint":
            return _Proc(code=1)
        if "-text" in args:
            return _Proc(b"Time stamp: Oct 11 10:55:00 2026 GMT\n" if failure=="late"
                         else b"Time stamp: Oct 11 09:27:30 2026 GMT\n")
        return _Proc()
    with pytest.raises(ExternalProofRefused):
        verify_rfc3161(
            statement, issued_at_t0=ISSUED, query_path=tmp_path/"q.tsq",
            response_path=tmp_path/"r.tsr", trust_ca_path=tmp_path/"external-ca.pem",
            runner=fake,
        )


def test_tsa_preimage_is_canonical_journal_payload_not_caller_recreated_forecast(tmp_path):
    from birzha.application.g2_time_anchor import commitment_from_atomic_staging
    from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
    from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
    from test_g2_freeze_real_forecast_from_source_receipts import NOW, source_fixture
    source = tmp_path/"original"
    stage = tmp_path/"staging"
    source_fixture(source)
    stage.mkdir()
    captured = issue_atomic_staging(source,stage,"SBER",clock=lambda:NOW)
    reopened = SingleDuckDBProspectiveStagingJournal(
        stage/"SBER.duckdb",staging_root=stage,clock=lambda:NOW
    )
    try:
        first = commitment_from_atomic_staging(reopened,captured["forecast_id"])
        assert commitment_from_atomic_staging(reopened,captured["forecast_id"])==first
        statement = json.loads(first)
        assert statement["forecast_id"] == captured["forecast_id"]
        assert statement["original_source_manifest_sha256"] == sha256(
            (source/"manifest.json").read_bytes()
        ).hexdigest()
    finally:
        reopened.close()


def test_exact_history_witness_is_tied_to_canonical_stored_D1_source(tmp_path):
    from birzha.application.g2_calendar_witness import concordance_from_atomic_staging
    from birzha.storage.prospective_single_store import SingleDuckDBProspectiveStagingJournal
    from scripts.g2_issue_sourcebound_atomic_staging import issue_atomic_staging
    from test_g2_freeze_real_forecast_from_source_receipts import NOW, source_fixture

    src=tmp_path/"raw"
    root=tmp_path/"isolated"
    source_fixture(src)
    root.mkdir()
    frozen=issue_atomic_staging(src,root,"SBER",clock=lambda:NOW)
    store=SingleDuckDBProspectiveStagingJournal(
        root/"SBER.duckdb",staging_root=root,clock=lambda:NOW
    )
    try:
        witness=_witness()
        witness["pages"][0]["observed_start_utc"]="2026-10-10T15:00:02Z"
        witness["pages"][0]["observed_end_utc"]="2026-10-10T15:00:07Z"
        outcome=concordance_from_atomic_staging(
            store, frozen["forecast_id"],witness
        )
        assert outcome["immutable_forecast_id"] == frozen["forecast_id"]
        assert outcome["last_active_history_session"] == "2026-10-09"
        assert outcome["exchange_schedule_independently_certified"] is False
        witness["board"] = "OTHER"
        with pytest.raises(CalendarWitnessRefused):
            concordance_from_atomic_staging(
                store, frozen["forecast_id"], witness
            )
    finally:
        store.close()
