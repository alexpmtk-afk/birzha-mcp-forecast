"""Q-097: full mocked source -> original journal -> M15 -> session -> NO-GO."""
from copy import deepcopy
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


def chain(tmp_path):
    provider,journal,clock,urls=environment(tmp_path)
    try:
        response=capture(provider,journal,clock)
        original=journal.pages(capture_key="CAPTURE",instrument=inst())
        schedule=witness()
        q088=preflight(pages=original,session_notice=schedule,candidate=candidate())
        q096=check_normalized_session_segments(
            preflight=q088,instrument=inst(),notice=schedule)
        return response,q088,q096,manifest()
    finally:
        journal.close()


def test_end_to_end_original_mock_source_is_exactly_bound_but_release_no_go(tmp_path):
    response,q088,q096,release=chain(tmp_path)
    got=audit_source_chain_no_go(
        governed_capture=response,preflight=q088,
        segmented_session=q096,release_manifest=release)
    assert got["status"]=="RESEARCH_CHAIN_CONTENT_PASS_PRODUCTION_NO_GO"
    assert got["source_m1_sha256"]==response["original_raw_sha256"]
    assert got["q088_commitment_sha256"]==q088["commitment_sha256"]
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
def test_adversarial_chain_or_release_forgery_fails_closed(tmp_path,change):
    source,q088,segments,release=chain(tmp_path)
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
            segmented_session=segments,release_manifest=release)


def test_normalized_calendar_and_local_clock_do_not_close_release_gates(tmp_path):
    source,q088,segments,release=chain(tmp_path)
    got=audit_source_chain_no_go(governed_capture=source,
        preflight=q088,segmented_session=segments,release_manifest=release)
    assert "exchange_calendar_authenticated" in got["unverified_required_facts"]
    assert "verified_first_network_receipt" in got["unverified_required_facts"]
    assert "trusted_independent_time_attestation" in got["unverified_required_facts"]
    assert "forecast_or_trade_authorized" in got["unverified_required_facts"]
    assert got["unresolved_blocker_count"]==12
