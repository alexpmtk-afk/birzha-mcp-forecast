"""Q-097: full mocked source -> original journal -> M15 -> session -> NO-GO."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import pytest

from birzha.application.g2_source_chain_no_go_audit import (
    SourceChainAuditRefused, audit_source_chain_no_go,
)
from birzha.application.g2_m15_segmented_session_guard import check_normalized_session_segments
from test_g2_governed_m1_intake import environment,capture
from test_g2_m15_segmented_session_guard import witness
from test_g2_m15_staging_preflight import preflight
from test_g2_m1_m15_source_replay import inst,candidate


def manifest():
    return json.loads(
        (Path(__file__).resolve().parents[1] /
         "docs/G2_RELEASE_GATE_20261010.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def chain_env(tmp_path):
    provider,journal,clock,urls=environment(tmp_path)
    try:
        response=capture(provider,journal,clock)
        original=journal.pages(capture_key="CAPTURE",instrument=inst())
        schedule=witness()
        q088=preflight(pages=original,session_notice=schedule,candidate=candidate())
        q096=check_normalized_session_segments(
            preflight=q088,instrument=inst(),notice=schedule)
        context={
            "intake":journal,"capture_key":"CAPTURE",
            "instrument":inst(),"candidate":candidate(),
            "session_notice":schedule,
            "evaluated_at":datetime(2026,10,9,7,16,12,tzinfo=timezone.utc),
        }
        yield response,q088,q096,manifest(),context
    finally:
        journal.close()


def test_end_to_end_original_mock_source_is_exactly_bound_but_release_no_go(chain_env):
    response,q088,q096,release,ctx=chain_env
    got=audit_source_chain_no_go(
        governed_capture=response,preflight=q088,
        segmented_session=q096,release_manifest=release,**ctx)
    assert got["status"]=="RESEARCH_CHAIN_CONTENT_PASS_PRODUCTION_NO_GO"
    assert got["source_m1_sha256"]==response["original_raw_sha256"]
    assert got["q088_commitment_sha256"]==q088["commitment_sha256"]
    assert len(got["q090_local_handoff_sha256"])==64
    assert got["session_segment_commitment_sha256"]==q096["commitment_sha256"]
    assert got["unresolved_blocker_count"]==12
    assert "independent_exchange_calendar_and_clock" in got["unresolved_blocker_names"]
    assert "historical_first_receipt_attestation" in got["unresolved_blocker_names"]
    assert "user_approved_main_merge" in got["unresolved_blocker_names"]
    assert "user_approved_home_deploy" in got["unresolved_blocker_names"]
    assert got["main_merge_authorized"] is False
    assert got["home_deploy_authorized"] is False
    assert got["release_allowed"] is False


@pytest.mark.parametrize("change",[
    "source_sha","source_route","source_instrument","source_released",
    "source_claimed_live","preflight_sha","preflight_bytes","preflight_released",
    "segment_sha","segment_swapped_q088","segment_certified","segment_released",
    "manifest_fake_approval","manifest_missing_gate","manifest_wrong_pin",
])
def test_adversarial_chain_or_release_forgery_fails_closed(chain_env,change):
    source,q088,segments,release,ctx=chain_env
    source,q088,segments,release=map(deepcopy,(source,q088,segments,release))
    if change=="source_sha":source["original_raw_sha256"]="0"*64
    elif change=="source_route":source["request_url_claim"]+="&interval=15"
    elif change=="source_instrument":source["instrument"]="stock/shares/TQBR/GAZP"
    elif change=="source_released":source["production_authorized"]=True
    elif change=="source_claimed_live":source["real_moex_transport_used"]=True
    elif change=="preflight_sha":q088["commitment_sha256"]="0"*64
    elif change=="preflight_bytes":q088["commitment_bytes"]+=b"forged"
    elif change=="preflight_released":q088["level_journal_admission_authorized"]=True
    elif change=="segment_sha":segments["commitment_sha256"]="0"*64
    elif change=="segment_swapped_q088":
        segments["manifest"]["original_q088_sha256"]="0"*64
    elif change=="segment_certified":
        segments["manifest"]["market_session_independently_authenticated"]=True
    elif change=="segment_released":segments["production_authorized"]=True
    elif change=="manifest_fake_approval":
        release["user_approved_main_merge"]=True
    elif change=="manifest_missing_gate":
        release["release_gates"].pop("independent_exchange_calendar_and_clock")
    elif change=="manifest_wrong_pin":
        release["release_leaves"][0]["head"]="0"*40
    with pytest.raises(SourceChainAuditRefused):
        audit_source_chain_no_go(
            governed_capture=source,preflight=q088,
            segmented_session=segments,release_manifest=release,**ctx)


def test_normalized_calendar_and_local_clock_do_not_close_release_gates(chain_env):
    source,q088,segments,release,ctx=chain_env
    got=audit_source_chain_no_go(governed_capture=source,
        preflight=q088,segmented_session=segments,release_manifest=release,**ctx)
    assert "exchange_calendar_authenticated" in got["unverified_required_facts"]
    assert "verified_first_network_receipt" in got["unverified_required_facts"]
    assert "trusted_independent_time_attestation" in got["unverified_required_facts"]
    assert "forecast_or_trade_authorized" in got["unverified_required_facts"]
    assert got["unresolved_blocker_count"]==12



@pytest.mark.parametrize("forgery",[
    "fully_rehashed_m15","rehashed_segment_index","receipt_timestamp",
    "receipt_seq","capture_key","claimed_callback_received",
    "canonical_gate_evidence","wrong_source_candidate","different_notice",
])
def test_recomputed_hashes_and_swapped_receipts_do_not_bypass_raw_replay(chain_env,forgery):
    from birzha.application.g2_m15_staging_preflight import _json
    source,q088,segments,release,ctx=chain_env
    source,q088,segments,release=map(deepcopy,(source,q088,segments,release))
    ctx=dict(ctx)
    if forgery=="fully_rehashed_m15":
        # Previously all staged hashes were internally correct, but the
        # M15 close did not correspond to the retained original Q090 bytes.
        q088["manifest"]["m15_from_original_pages"]["close"]+=0.5
        q088["commitment_bytes"]=_json(q088["manifest"])
        q088["commitment_sha256"]=sha256(q088["commitment_bytes"]).hexdigest()
        segments["manifest"]["original_q088_sha256"]=q088["commitment_sha256"]
        segments["commitment_sha256"]=sha256(_json(segments["manifest"])).hexdigest()
    elif forgery=="rehashed_segment_index":
        segments["manifest"]["accepted_continuous_segment_index"]=23
        segments["commitment_sha256"]=sha256(_json(segments["manifest"])).hexdigest()
    elif forgery=="receipt_timestamp":
        source["receipt"]["first_local_received_utc"]="2026-10-09T07:16:01Z"
    elif forgery=="receipt_seq":
        source["receipt"]["receipt_seq"]=999
    elif forgery=="capture_key":
        source["capture_key"]="UNRELATED_CAPTURE"
    elif forgery=="claimed_callback_received":
        source["callback_response_utc"]="2026-10-09T07:16:03Z"
    elif forgery=="canonical_gate_evidence":
        release["release_gates"]["future_outcomes_5_10_20"]["evidence"]="FORGED FACT"
    elif forgery=="wrong_source_candidate":
        from dataclasses import replace
        ctx["candidate"]=replace(ctx["candidate"],close=ctx["candidate"].close+1)
    elif forgery=="different_notice":
        from dataclasses import replace
        ctx["session_notice"]=replace(ctx["session_notice"],session_identifier="OTHER")
    with pytest.raises(SourceChainAuditRefused):
        audit_source_chain_no_go(governed_capture=source,
            preflight=q088,segmented_session=segments,
            release_manifest=release,**ctx)


def test_mutated_original_sqlite_source_fails_despite_unchanged_self_signed_summaries(chain_env):
    import sqlite3
    source,q088,segments,release,ctx=chain_env
    db=ctx["intake"].db
    db.execute("DROP TRIGGER m1_local_no_update")
    db.execute("UPDATE m1_local_receipts SET raw=? WHERE seq=1",(b'{"fake":true}',))
    with pytest.raises(SourceChainAuditRefused):
        audit_source_chain_no_go(governed_capture=source,
            preflight=q088,segmented_session=segments,
            release_manifest=release,**ctx)
