"""Q-089 RFC3161 proof of Q-088 packet content. Mocked openssl only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from birzha.application.g2_m15_external_time_proof import (
    M15TimeProofRefused, verify_m15_rfc3161, VERSION,
)
from test_g2_m15_staging_preflight import preflight, notice
from test_g2_m1_m15_source_replay import candidate, make_page, inst


class FakeProc:
    def __init__(self, output=b"", returncode=0):
        self.stdout,self.stderr,self.returncode=output,b"",returncode


def fake_verifier(args, input=None, **kwargs):
    if "-query" in args and "-text" in args:
        return FakeProc(b"Nonce: 0x112233445566778899AABBCC\n")
    if "-verify" in args:
        return FakeProc()
    if "-reply" in args and "-text" in args:
        return FakeProc(b"Time stamp: Oct  9 07:16:40 2026 GMT\n")
    return FakeProc(returncode=1)


def original_files(tmp_path):
    for name,value in [
        ("original.tsq",b"MOCKED_NONCE_BEARING_QUERY"),
        ("signed.tsr",b"MOCKED_NOT_AN_ACTUAL_SIGNATURE"),
        ("trust.pem",b"MOCKED_NOT_AN_ACTUAL_TRUSTED_CA"),
    ]:
        (tmp_path/name).write_bytes(value)
    return dict(query_path=tmp_path/"original.tsq",
                response_path=tmp_path/"signed.tsr",
                trust_ca_path=tmp_path/"trust.pem")


def accepted(tmp_path, **overrides):
    kw=original_files(tmp_path)
    kw.update(overrides)
    return verify_m15_rfc3161(
        preflight=kw.pop("preflight",preflight()),
        instrument=kw.pop("instrument",inst()),
        pages=kw.pop("pages",(make_page(),)),
        candidate=kw.pop("candidate",candidate()),
        session_notice=kw.pop("session_notice",notice()),
        runner=kw.pop("runner",fake_verifier),
        **kw)


def test_existing_preflight_bytes_must_be_replayed_and_commitment_signed(tmp_path):
    seen=[]
    def checking(args, input=None, **kwargs):
        seen.append(args)
        return fake_verifier(args,input=input,**kwargs)
    p=preflight()
    out=accepted(tmp_path,preflight=p,runner=checking)
    assert out["schema"]==VERSION
    assert out["m15_preflight_commitment_sha256"]==sha256(p["commitment_bytes"]).hexdigest()
    assert any("-queryfile" in a for a in seen)
    assert any("-digest" in a and p["commitment_sha256"] in a for a in seen)
    assert out["tsa_signed_time_utc"]=="2026-10-09T07:16:40Z"
    assert out["external_ca_independently_approved"] is False
    assert out["earliest_original_market_receipt_proven"] is False
    assert out["independent_moex_provider_authentication"] is False
    assert out["official_session_calendar_independently_verified"] is False
    assert out["level_journal_admission_authorized"] is False
    assert out["production_authorized"] is False


@pytest.mark.parametrize("alter",[
    "wrong_packet_bytes","wrong_sha","wrong_session","wrong_raw_page",
    "wrong_candle","forged_status","forged_authorization","changed_manifest",
])
def test_cannot_sign_different_preflight_without_replay_match(tmp_path,alter):
    p=preflight(); options={}
    if alter=="wrong_packet_bytes":p={**p,"commitment_bytes":p["commitment_bytes"]+b"alter"}
    elif alter=="wrong_sha":p={**p,"commitment_sha256":"0"*64}
    elif alter=="wrong_session":options["session_notice"]=replace(notice(),session_identifier="FORGED")
    elif alter=="wrong_raw_page":
        v=make_page()
        raw=v.raw_body.replace(b"101.0",b"101.1",1)
        options["pages"]=(replace(v,raw_body=raw,raw_sha256=sha256(raw).hexdigest()),)
    elif alter=="wrong_candle":options["candidate"]=replace(candidate(),close=candidate().close+1)
    elif alter=="forged_status":p={**p,"status":"TRUSTED"}
    elif alter=="forged_authorization":p={**p,"level_journal_admission_authorized":True}
    elif alter=="changed_manifest":
        p={**p,"manifest":{**p["manifest"],"source_provider_authentication_status":"TRUSTED"}}
    with pytest.raises(M15TimeProofRefused):
        accepted(tmp_path,preflight=p,**options)


@pytest.mark.parametrize("mode",[
    "missing_nonce","short_nonce","query_signature_failure","digest_failure",
    "missing_signature_time","bad_signature_time","before_preflight",
    "after_window","runner_error","runner_exception",
])
def test_unverifiable_timestamp_never_succeeds(tmp_path,mode):
    def invalid(args, input=None, **kwargs):
        if mode=="runner_exception":
            raise OSError("openssl not available")
        if mode=="runner_error":
            return FakeProc(returncode=1)
        if "-query" in args:
            if mode=="missing_nonce":return FakeProc(b"Nonce: missing\n")
            if mode=="short_nonce":return FakeProc(b"Nonce: 0x12\n")
        if mode=="query_signature_failure" and "-queryfile" in args:
            return FakeProc(returncode=1)
        if mode=="digest_failure" and "-digest" in args:
            return FakeProc(returncode=1)
        if "-reply" in args:
            mapping={
                "missing_signature_time":b"Serial number: 13\n",
                "bad_signature_time":b"Time stamp: malformed\n",
                "before_preflight":b"Time stamp: Oct  9 07:15:20 2026 GMT\n",
                "after_window":b"Time stamp: Oct  9 07:22:00 2026 GMT\n",
            }
            if mode in mapping:return FakeProc(mapping[mode])
        return fake_verifier(args,input=input,**kwargs)
    with pytest.raises(M15TimeProofRefused):
        accepted(tmp_path,runner=invalid)


@pytest.mark.parametrize("material",["query","response","ca"])
def test_missing_or_symlinked_artifact_fails_closed(tmp_path,material):
    files=original_files(tmp_path)
    name={"query":"query_path","response":"response_path","ca":"trust_ca_path"}[material]
    files[name].unlink()
    with pytest.raises(M15TimeProofRefused):
        accepted(tmp_path,**{name:files[name]})
    files[name].symlink_to(tmp_path/"somewhere",target_is_directory=False)
    with pytest.raises(M15TimeProofRefused):
        accepted(tmp_path,**{name:files[name]})


def test_external_signed_time_is_existence_upper_bound_not_receipt_time(tmp_path):
    result=accepted(tmp_path)
    assert result["status"]=="SIGNED_Q088_COMMITMENT_CHECKED_UNDER_CALLER_CA"
    assert result["earliest_original_market_receipt_proven"] is False
    assert result["future_outcome_boundary_certified"] is False
    assert result["forecast_or_trade_authorized"] is False
