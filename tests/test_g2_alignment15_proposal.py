"""Exact same-snapshot alignment score proposal, no external trading acceptance."""
from dataclasses import replace

import pytest

from birzha.application.g2_alignment15_proposal import (
    WEIGHTS, _score, propose_alignment_and_no_trade,
)
from birzha.domain.volume_profile import VolumeProfileResult
from test_g2_level_route import prepared, _profile


def with_real_profile_candidate(snapshot, *, val=110.,poc=112.,vah=114.):
    # The fixture asserts a syntactically exact profile: still synthetic and
    # NEVER independently authenticated as real public trades.
    profile=VolumeProfileResult(
        poc=poc,vah=vah,val=val,value_area_fraction=.7,
        hvn=(poc+.25,),lvn=(val+.25,),shape="D",bins=(),
        total_volume=10_000.,method="PUBLIC_TRADES_PRICE_QUANTITY_V1",
    )
    return replace(
        snapshot,flow=replace(snapshot.flow,volume_profile=profile),
        volume_profile=profile,
        normalized_features=replace(snapshot.normalized_features,
            profile_method=profile.method,profile_is_exact=True),
    )


def test_draft_weight_proposal_exactly_15_and_distinguishes_both_sides():
    assert WEIGHTS=={
        "market_state":2,"control":3,"location":2,"pressure":1,
        "efficiency":2,"effort_result":2,"route":3,
    }
    snap,inst,m15=prepared("BUYERS")
    snap=with_real_profile_candidate(snap)
    original=snap.to_dict()
    proposal=propose_alignment_and_no_trade(snap,inst,m15)
    assert proposal["location_exact_profile_candidate"]=="BELOW_VAL"
    assert proposal["scorecards"]["LONG"]["total_score_out_of_15_not_probability"]==12
    assert proposal["scorecards"]["SHORT"]["total_score_out_of_15_not_probability"]==5
    assert proposal["scorecards"]["LONG"]["factors"]["pressure"]["points"]==0
    assert proposal["scorecards"]["SHORT"]["factors"]["pressure"]["points"]==1
    assert proposal["selected_decision"]=="NO_TRADE"
    assert proposal["engine3_accepted"] is False
    assert proposal["user_scoring_formula_approved"] is False
    assert proposal["scenario_or_execution_authorized"] is False
    assert proposal["primary_scenario"] is None
    assert proposal["execution_entry"] is None
    assert proposal["production_authorized"] is False
    assert snap.to_dict()==original


def test_missing_verified_location_is_null_not_zero_and_abstains():
    snap,inst,m15=prepared("BUYERS")
    result=propose_alignment_and_no_trade(snap,inst,m15)
    assert result["location_exact_profile_candidate"] is None
    assert result["scorecards"]["LONG"]["total_score_out_of_15_not_probability"] is None
    assert result["scorecards"]["LONG"]["factors"]["location"]["points"] is None
    assert "EXACT_LOCATION_UNAVAILABLE" in result["reason_codes"]
    assert result["selected_decision"]=="NO_TRADE"


def test_candle_proxy_must_not_become_approved_location():
    snap,inst,m15=prepared("BUYERS")
    fake=_profile("CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1")
    snap=replace(snap,volume_profile=fake,
                 normalized_features=replace(snap.normalized_features,
                    profile_method=fake.method,profile_is_exact=False))
    p=propose_alignment_and_no_trade(snap,inst,m15)
    assert p["scorecards"]["LONG"]["factors"]["location"]["points"] is None
    assert p["selected_decision"]=="NO_TRADE"


def test_claimed_exact_profile_without_matching_flow_cannot_score_location():
    snap,inst,m15=prepared("BUYERS")
    profile=_profile("PUBLIC_TRADES_PRICE_QUANTITY_V1")
    snap=replace(snap,volume_profile=profile,
                 normalized_features=replace(snap.normalized_features,
                     profile_method=profile.method,profile_is_exact=True))
    p=propose_alignment_and_no_trade(snap,inst,m15)
    assert p["location_exact_profile_candidate"] is None
    assert p["scorecards"]["LONG"]["total_score_out_of_15_not_probability"] is None
    assert p["selected_decision"]=="NO_TRADE"


def test_missing_delta_is_unknown_not_negative_pressure():
    snap,inst,m15=prepared("BUYERS")
    snap=replace(with_real_profile_candidate(snap),flow=None)
    p=propose_alignment_and_no_trade(snap,inst,m15)
    for side in ("LONG","SHORT"):
        assert p["scorecards"][side]["factors"]["pressure"]["points"] is None
        assert p["scorecards"][side]["total_score_out_of_15_not_probability"] is None


def test_conflicting_regime_denies_route_even_when_control_holds():
    snap,inst,m15=prepared("BUYERS")
    snap=with_real_profile_candidate(snap)
    f=replace(snap.normalized_features,
        d1_return_5_atr=-2,h1_return_5_atr=-1.5,
        m15_return_5_atr=-1.1,
        d1_price_location_20=.15,h1_price_location_20=.15,
        m15_price_location_20=.15)
    p=propose_alignment_and_no_trade(replace(snap,normalized_features=f),inst,m15)
    assert p["scorecards"]["LONG"]["factors"]["route"]["points"] is None
    assert p["selected_decision"]=="NO_TRADE"
    assert "CAUSAL_ROUTE_UNAVAILABLE" in p["reason_codes"]


def test_malformed_m15_candle_prevents_scenarios_despite_valid_feature_vector():
    snap,inst,m15=prepared()
    candles=list(m15.candles)
    candles[-1]=replace(candles[-1],completed=False)
    p=propose_alignment_and_no_trade(snap,inst,replace(m15,candles=tuple(candles)))
    assert p["scorecards"]["LONG"]["total_score_out_of_15_not_probability"] is None
    assert p["selected_decision"]=="NO_TRADE"


def test_invalid_caller_dictionary_cannot_override_recomputed_components():
    snap,inst,m15=prepared()
    with pytest.raises(TypeError):
        propose_alignment_and_no_trade(snap,inst,{"control":"BUYERS"})
    with pytest.raises(TypeError):
        propose_alignment_and_no_trade({"market_state":"TREND_UP"},inst,m15)


def test_neither_external_formulas_nor_outcomes_are_edited():
    snap,inst,m15=prepared()
    p=propose_alignment_and_no_trade(snap,inst,m15)
    assert p["existing_immutable_forecast_modified"] is False
    assert p["targets"]==[]
    assert p["stop"] is None
    assert p["invalidation"] is None
    assert p["reversal"] is None


def test_pure_score_fail_closed_on_untrusted_missing_factors():
    snap,inst,m15=prepared()
    from birzha.application.level_route import assess_levels_and_route
    from birzha.application.control_holding import control_holding_from_snapshot
    from birzha.application.market_state_classifier import classify_market_state_from_snapshot
    r=assess_levels_and_route(snap,inst,m15)
    h=control_holding_from_snapshot(snap,inst,m15)
    s=classify_market_state_from_snapshot(snap,inst)
    score=_score("LONG",snap,s,h,r,None)
    assert score["complete_proposed_scorecard"] is False
    assert score["total_score_out_of_15_not_probability"] is None
