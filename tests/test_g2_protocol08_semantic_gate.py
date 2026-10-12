"""Protocol 08 structural compliance tests: no accidental release or orders."""
from dataclasses import replace
from math import nan, inf

import pytest

from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.g2_protocol08_semantic_gate import (
    audit_protocol08_forecast, evaluate_mandatory_conditions,
)
from test_forecast_engine import _state
from birzha.domain.snapshot import MarketSnapshot


def baseline():
    return build_forecast_from_snapshot(MarketSnapshot(
        symbol="SBER", secid="SBER", as_of="2026-10-10T15:00:20Z",
        source="OFFLINE_SYNTHETIC_FIXTURE",
        d1=_state("D1", 3.0, .02, .01),
        h1=_state("H1", 2.5, .01, .01),
        m15=_state("M15", 2.0, .005, .01),
        data_quality="PASS", warnings=(),
    ))


def test_real_baseline_labels_are_not_misrepresented_as_protocol08_control():
    fcst = baseline()
    assert fcst.control == "BUYERS"
    assert fcst.route == "TREND"
    original = fcst.to_dict()
    report = audit_protocol08_forecast(fcst)
    assert report["fields"]["snapshot_binding"]["status"] == "STRUCTURE_PRESENT"
    assert report["fields"]["control_holding_confidence"]["status"] == "RESEARCH_PRESENT_UNVERIFIED"
    assert report["fields"]["route_obstacles_and_confidence"]["status"] == "RESEARCH_PRESENT_UNVERIFIED"
    assert report["fields"]["alignment_scoring_formula"]["status"] == "RESEARCH_PROPOSAL_UNAPPROVED"
    assert report["fields"]["market_state_classifier"]["status"] == "RESEARCH_PRESENT_UNVERIFIED"
    assert report["full_protocol08_accepted"] is False
    assert report["trade_executable"] is False
    assert report["production_authorized"] is False
    assert fcst.to_dict() == original


def test_forged_validated_forecast_cannot_upgrade_unsupported_model():
    fcst = replace(
        baseline(), validation_status="VALIDATED_BY_UNTRUSTED_CALLER",
        market_state="TREND_UP", location="ABOVE_VAH",
        pressure="BUYERS_FAVORED", alignment="15",
        entry_levels=(101.0,), stop_level=97.5,
        target_levels=(105.0, 109.0), reversal_condition="NEW_ANALYSIS",
        primary_scenario="BUY NOW",
    )
    outcome=audit_protocol08_forecast(fcst)
    assert outcome["full_protocol08_accepted"] is False
    assert outcome["fields"]["control_holding_confidence"]["status"] == "RESEARCH_PRESENT_UNVERIFIED"
    assert outcome["fields"]["route_obstacles_and_confidence"]["status"] == "RESEARCH_PRESENT_UNVERIFIED"
    assert outcome["fields"]["alignment_scoring_formula"]["status"] == "RESEARCH_PROPOSAL_UNAPPROVED"
    assert outcome["trade_executable"] is False


def test_matching_unclassified_market_state_vector_is_not_regime_certification():
    fcst=baseline()
    vector={"snapshot_id":fcst.snapshot_id,"symbol":"SBER","secid":"SBER",
            "as_of":fcst.created_at_t0,"classification_status":"NOT_CLASSIFIED"}
    observed=audit_protocol08_forecast(fcst,market_state_vector=vector)
    assert observed["market_state_vector_matched"] is True
    assert observed["market_state_vector_classified"] is False
    assert observed["fields"]["market_state_classifier"]["status"] == "RESEARCH_PRESENT_UNVERIFIED"


def test_mismatched_snapshot_time_contract_does_not_pass_semantic_audit():
    fcst=baseline()
    vector={"snapshot_id":fcst.snapshot_id,"symbol":fcst.symbol,
            "secid":fcst.secid,"as_of":"2026-10-11T15:00:20Z",
            "classification_status":"CLASSIFIED"}
    with pytest.raises(ValueError,match="frozen Forecast T0"):
        audit_protocol08_forecast(fcst,market_state_vector=vector)


def test_naive_time_may_be_a_legacy_record_but_is_not_secure_t0():
    fcst=replace(baseline(),created_at_t0="2026-10-10T15:00:20")
    report=audit_protocol08_forecast(fcst)
    assert report["fields"]["snapshot_binding"]["status"]=="MISSING"
    assert report["fields"]["independent_source_and_time_attestation"]["status"]=="NOT_VERIFIED"


@pytest.mark.parametrize("side,control,confidence,route,align,reason",[
    ("LONG","SELLERS",9,"HIGH",15,"CONTROL_SIDE_MISMATCH"),
    ("SHORT","BUYERS",9,"HIGH",15,"CONTROL_SIDE_MISMATCH"),
    ("LONG","BUYERS",5.9,"HIGH",15,"CONTROL_CONFIDENCE_BELOW_6"),
    ("LONG","BUYERS",None,"HIGH",15,"CONTROL_CONFIDENCE_MISSING"),
    ("LONG","BUYERS",9,"LOW",15,"ROUTE_CONFIDENCE_LOW"),
    ("LONG","BUYERS",9,None,15,"ROUTE_CONFIDENCE_MISSING"),
    ("LONG","BUYERS",9,"HIGH",6.999,"ALIGNMENT_BELOW_7"),
    ("LONG","BUYERS",9,"HIGH",None,"ALIGNMENT_SCORE_MISSING"),
])
def test_mandatory_conditions_not_bypassed_by_high_alignment(side,control,confidence,route,align,reason):
    x=evaluate_mandatory_conditions(
        side=side,control=control,control_confidence=confidence,
        route_confidence=route,alignment_score=align,
        scoring_formula_approved=True,market_state="TREND_UP",
    )
    assert reason in x["blocking_reasons"]
    assert x["status"]=="NO_TRADE_OR_WITHHOLD_SCENARIO"
    assert x["execution_authorized"] is False


def test_even_perfect_inputs_are_not_an_approved_trading_engine():
    missing=evaluate_mandatory_conditions(
        side="LONG",control="BUYERS",control_confidence=10,
        route_confidence="HIGH",alignment_score=15,market_state="TREND_UP",
    )
    assert "ALIGNMENT_FORMULA_TBD" in missing["blocking_reasons"]
    assert missing["status"]=="NO_TRADE_OR_WITHHOLD_SCENARIO"

    claimed=evaluate_mandatory_conditions(
        side="LONG",control="BUYERS",control_confidence=10,
        route_confidence="HIGH",alignment_score=15,
        scoring_formula_approved=True,market_state="TREND_UP",
    )
    assert claimed["status"]=="CANDIDATE_CONDITIONAL_NOT_EXECUTABLE"
    assert claimed["scoring_formula_independently_certified"] is False
    assert claimed["execution_authorized"] is False
    assert claimed["production_authorized"] is False


def test_between_seven_and_ten_stays_confirmation_required():
    x=evaluate_mandatory_conditions(
        side="SHORT",control="SELLERS",control_confidence=6,
        route_confidence="MEDIUM",alignment_score=9.9,
        scoring_formula_approved=True,market_state="DISTRIBUTION",
    )
    assert "ALIGNMENT_7_TO_10_NEEDS_CONFIRMATION" in x["blocking_reasons"]
    assert x["status"]=="NO_TRADE_OR_WITHHOLD_SCENARIO"


def test_balance_disallows_route_even_when_scoring_claims_perfect():
    x=evaluate_mandatory_conditions(
        side="LONG",control="BUYERS",control_confidence=10,
        route_confidence="HIGH",alignment_score=15,
        scoring_formula_approved=True,market_state="BALANCE",
    )
    assert "ROUTE_FORBIDDEN_IN_BALANCE" in x["blocking_reasons"]


@pytest.mark.parametrize("value,field",[
    (True,"confidence"),(-0.1,"confidence"),(11,"confidence"),
    (nan,"confidence"),(inf,"confidence"),
    (False,"alignment"),(-1,"alignment"),(15.001,"alignment"),
    (nan,"alignment"),(-inf,"alignment"),
])
def test_out_of_contract_numeric_conditions_are_refused(value,field):
    kw={"side":"LONG","control":"BUYERS","control_confidence":8,
        "route_confidence":"HIGH","alignment_score":12}
    kw["control_confidence" if field=="confidence" else "alignment_score"]=value
    with pytest.raises(ValueError):
        evaluate_mandatory_conditions(**kw)


@pytest.mark.parametrize("field,value",[
    ("control","LONG"),("route_confidence","UNVERIFIED"),
    ("market_state","NOT_A_REGIME"),("side","NO_TRADE"),
    ("scoring_formula_approved","yes"),
])
def test_invalid_scenario_enums_and_claimed_approval_are_rejected(field,value):
    kw={"side":"LONG","control":"BUYERS","control_confidence":8,
        "route_confidence":"HIGH","alignment_score":12,
        "market_state":"TREND_UP"}
    kw[field]=value
    with pytest.raises(ValueError):
        evaluate_mandatory_conditions(**kw)


def test_research_engines_are_present_but_cannot_mark_protocol_complete():
    from birzha.application.control_holding import control_holding_from_snapshot
    from birzha.application.level_route import assess_levels_and_route
    from birzha.application.market_state_classifier import classify_market_state_from_snapshot
    from birzha.application.g2_alignment15_proposal import propose_alignment_and_no_trade
    assert all(callable(x) for x in (
        control_holding_from_snapshot, assess_levels_and_route,
        classify_market_state_from_snapshot, propose_alignment_and_no_trade
    ))
    original=baseline()
    audit=audit_protocol08_forecast(original)
    required={
        "market_state_classifier":"RESEARCH_PRESENT_UNVERIFIED",
        "control_holding_confidence":"RESEARCH_PRESENT_UNVERIFIED",
        "level_role_strength_and_history":"RESEARCH_PRESENT_UNVERIFIED",
        "route_obstacles_and_confidence":"RESEARCH_PRESENT_UNVERIFIED",
        "alignment_scoring_formula":"RESEARCH_PROPOSAL_UNAPPROVED",
        "scenario_mandatory_conditions":"RESEARCH_PARTIAL_UNVERIFIED",
    }
    for key,value in required.items():
        assert audit["fields"][key]["status"]==value
        assert key in audit["not_satisfied_for_full_protocol08"]
    assert audit["full_protocol08_accepted"] is False
    assert audit["trade_executable"] is False
    assert audit["production_authorized"] is False
