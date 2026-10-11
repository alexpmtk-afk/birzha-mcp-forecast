"""Level roles and obstacle Route derived without lookahead or caller-spoofed regime."""
from dataclasses import replace

import pytest

from birzha.application.level_route import assess_levels_and_route
from birzha.domain.volume_profile import VolumeProfileResult
from test_g2_control_holding import fixture


def prepared(side="BUYERS"):
    snapshot,instrument,series=fixture(side)
    positive=(side=="BUYERS")
    features=replace(
        snapshot.normalized_features,
        d1_return_5_atr=2 if positive else -2,
        h1_return_5_atr=1.5 if positive else -1.5,
        m15_return_5_atr=1.1 if positive else -1.1,
        d1_efficiency_20=.85,h1_efficiency_20=.85,m15_efficiency_20=.85,
        d1_price_location_20=.85 if positive else .15,
        h1_price_location_20=.85 if positive else .15,
        m15_price_location_20=.85 if positive else .15,
    )
    return replace(snapshot,normalized_features=features),instrument,series


@pytest.mark.parametrize("side,direction,obstacle",[
    ("BUYERS","UP","PRIOR_RESISTANCE"),
    ("SELLERS","DOWN","PRIOR_SUPPORT"),
])
def test_route_uses_prior_level_history_and_independent_same_snapshot_regime(
        side,direction,obstacle):
    snapshot,instrument,series=prepared(side)
    before=snapshot.to_dict()
    result=assess_levels_and_route(snapshot,instrument,series)
    assert result["classification_status"]=="PROVISIONAL_ROUTE_CANDIDATE"
    assert result["route_direction"]==direction
    assert result["control"]==side
    assert result["market_state_recomputed_status"]=="PROVISIONAL_CANDIDATE"
    assert result["free_space_atr"]==4.25
    assert result["route_confidence_research_only"]=="HIGH"
    assert result["nearest_obstacle"]["type"]==obstacle
    assert result["nearest_obstacle"]["distance_in_atr"]==4.25
    assert len(result["level_candidates"])==2
    for level in result["level_candidates"]:
        assert level["strength"]=="STRONG"
        assert level["role_flip_history_status"]=="NOT_ESTABLISHED_FROM_ROLLING_EXTREME"
        assert all(e["event"]=="LEVEL_TEST_HELD_AFTER_PRICE_FIRST_OBSERVABLE"
                   for e in level["observed_test_history"])
        assert level["price_first_observable_at_utc"] is not None
        assert all(e["source_observed_at_utc"]>level["price_first_observable_at_utc"]
                   for e in level["observed_test_history"])
        assert level["level_detected_ex_ante_proven"] is False
        assert level["history_reconstruction_status"]==(
            "POST_FIRST_OBSERVABLE_TESTS_RECONSTRUCTED_NOT_EX_ANTE_CERTIFIED")
        # The final *pre-reaction* completed bar is eligible as a retest,
        # so its end may equal the exact boundary of the prior-only window.
        assert all(e["observed_end_utc"]<=series.candles[-4].end.replace("+00:00","Z")
                   for e in level["observed_test_history"])
    assert result["source_independently_authenticated"] is False
    assert result["route_thresholds_externally_calibrated"] is False
    assert result["alignment_approved"] is False
    assert result["scenario_or_trade_authorized"] is False
    assert result["production_authorized"] is False
    assert snapshot.to_dict()==before


def test_abstains_if_obstacle_is_too_close_even_with_positive_holding():
    snapshot,instrument,series=prepared()
    bars=list(series.candles)
    bars[:47]=[replace(c,open=101.,high=102.,low=100.,close=101.)
               for c in bars[:47]]
    result=assess_levels_and_route(snapshot,instrument,
                                   replace(series,candles=tuple(bars)))
    assert result["classification_status"]=="ABSTAIN"
    assert result["free_space_atr"]==.25
    assert "INSUFFICIENT_SPACE_BEFORE_OBSTACLE" in result["reason_codes"]
    assert result["route_confidence_research_only"] is None


def test_missing_obstacle_does_not_infer_infinite_free_space():
    snapshot,instrument,series=prepared()
    bars=list(series.candles)
    # Support and resistance from prior extremes cannot lie above a new
    # breakout price; do not claim unlimited reward when no obstacle known.
    bars[-1]=replace(bars[-1],open=110.,high=112.,low=110.,close=111.)
    snapshot=replace(snapshot,m15=replace(snapshot.m15,last_close=111.))
    result=assess_levels_and_route(snapshot,instrument,
                                   replace(series,candles=tuple(bars)))
    assert result["classification_status"]=="ABSTAIN"
    assert "NO_VERIFIABLE_AHEAD_OBSTACLE" in result["reason_codes"] or (
        "CONTROL_HOLDING_NOT_CONFIRMED" in result["reason_codes"]
    )


def test_degraded_or_unobserved_candle_never_generates_route():
    snapshot,instrument,series=prepared()
    candles=list(series.candles)
    candles[-1]=replace(candles[-1],available_at_confidence="UNKNOWN")
    observed=assess_levels_and_route(snapshot,instrument,
                                    replace(series,candles=tuple(candles)))
    assert observed["classification_status"]=="ABSTAIN"
    assert "CONTROL_HOLDING_NOT_CONFIRMED" in observed["reason_codes"]
    assert observed["typed_obstacles_ahead"]==[]


def test_conflicting_regime_abstains_and_does_not_override_holding():
    snapshot,instrument,series=prepared()
    normalized=replace(snapshot.normalized_features,
                       d1_return_5_atr=-2,h1_return_5_atr=-1.5,
                       m15_return_5_atr=-1.1,
                       d1_price_location_20=.15,h1_price_location_20=.15,
                       m15_price_location_20=.15)
    snapshot=replace(snapshot,normalized_features=normalized)
    result=assess_levels_and_route(snapshot,instrument,series)
    assert result["control"]=="BUYERS"
    assert result["market_state_recomputed_regime"]=="TREND_DOWN"
    assert "CONTROL_CONTRADICTS_MARKET_STATE" in result["reason_codes"]
    assert result["classification_status"]=="ABSTAIN"


def test_neutral_market_state_cannot_be_promoted_to_route_by_price_only():
    snapshot,instrument,series=prepared()
    normalized=replace(snapshot.normalized_features,
                       d1_return_5_atr=0,h1_return_5_atr=0,
                       m15_return_5_atr=0,
                       d1_efficiency_20=.1,h1_efficiency_20=.1,
                       m15_efficiency_20=.1,
                       d1_price_location_20=.5,h1_price_location_20=.5,
                       m15_price_location_20=.5)
    snapshot=replace(snapshot,normalized_features=normalized)
    result=assess_levels_and_route(snapshot,instrument,series)
    assert result["market_state_recomputed_regime"]=="BALANCE"
    assert result["classification_status"]=="ABSTAIN"
    assert "MARKET_STATE_NOT_DIRECTIONAL_ROUTE" in result["reason_codes"]


def _profile(method):
    return VolumeProfileResult(
        poc=103.,vah=106.,val=101.,value_area_fraction=.7,
        hvn=(103.5,),lvn=(102.5,),shape="D",bins=(),
        total_volume=10000.,method=method,
    )


def test_approximate_candle_volume_profile_not_admitted_as_ahead_obstacle():
    snapshot,instrument,series=prepared()
    fake=_profile("CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1")
    snapshot=replace(
        snapshot,volume_profile=fake,
        normalized_features=replace(
            snapshot.normalized_features,profile_is_exact=False,
            profile_method=fake.method),
    )
    result=assess_levels_and_route(snapshot,instrument,series)
    assert result["profile_admission_reason"]=="APPROXIMATE_OR_UNVERIFIED_VOLUME_PROFILE_EXCLUDED"
    assert len(result["level_candidates"])==2
    assert not any(x["type"]=="POC" for x in result["typed_obstacles_ahead"])


def test_declared_exact_profile_with_no_matching_original_flow_refused():
    snapshot,instrument,series=prepared()
    exact=_profile("PUBLIC_TRADES_PRICE_QUANTITY_V1")
    snapshot=replace(
        snapshot,volume_profile=exact,
        normalized_features=replace(snapshot.normalized_features,
                                    profile_method=exact.method,
                                    profile_is_exact=True),
    )
    outcome=assess_levels_and_route(snapshot,instrument,series)
    assert outcome["classification_status"]=="ABSTAIN"
    assert "CLAIMED_EXACT_PROFILE_FAILED_PROVENANCE" in outcome["reason_codes"]


def test_exact_matching_original_flow_profile_is_typed_but_not_independently_trusted():
    snapshot,instrument,series=prepared()
    exact=_profile("PUBLIC_TRADES_PRICE_QUANTITY_V1")
    flow=replace(snapshot.flow,volume_profile=exact)
    snapshot=replace(snapshot,flow=flow,volume_profile=exact,
                     normalized_features=replace(
                         snapshot.normalized_features,profile_method=exact.method,
                         profile_is_exact=True))
    outcome=assess_levels_and_route(snapshot,instrument,series)
    assert outcome["classification_status"]=="ABSTAIN" # POC at 103 = only .75 ATR
    assert outcome["nearest_obstacle"]["type"]=="LVN"
    assert any(x["type"]=="POC" for x in outcome["typed_obstacles_ahead"])
    assert "INSUFFICIENT_SPACE_BEFORE_OBSTACLE" in outcome["reason_codes"]
    assert outcome["profile_trade_origin_independently_certified"] is False


def test_type_and_payload_boundary_rejects_caller_spoofed_regime():
    snap,ins,series=prepared()
    with pytest.raises(TypeError):
        assess_levels_and_route(snap,ins,{"candles":series.candles})
    with pytest.raises(TypeError):
        assess_levels_and_route(snap,ins,series,market_state_candidate={
            "candidate_regime":"TREND_UP","classification_status":"CLASSIFIED",
        })


def test_profile_flow_older_than_last_completed_m15_is_not_used_as_location_or_obstacle():
    snapshot, instrument, series = prepared("BUYERS")
    exact = _profile("PUBLIC_TRADES_PRICE_QUANTITY_V1")
    flow = replace(snapshot.flow, volume_profile=exact,
                   as_of="2026-10-10T12:29:00Z")
    snap = replace(snapshot, flow=flow, volume_profile=exact,
                   normalized_features=replace(snapshot.normalized_features,
                                               profile_method=exact.method,
                                               profile_is_exact=True))
    route = assess_levels_and_route(snap, instrument, series)
    assert route["profile_admission_reason"] == "PROFILE_FLOW_PREDATES_LAST_COMPLETED_M15"
    assert not any(item["exact_trade_volume_profile"]
                   for item in route["level_candidates"])
    assert "CLAIMED_EXACT_PROFILE_FAILED_PROVENANCE" in route["reason_codes"]
    assert route["scenario_or_trade_authorized"] is False


@pytest.mark.parametrize("side,level_kind", [
    ("BUYERS", "PRIOR_SUPPORT"), ("SELLERS", "PRIOR_RESISTANCE"),
])
def test_historical_near_touches_preceding_first_exact_extreme_are_not_tests(
        side,level_kind):
    snapshot,instrument,series=prepared(side)
    bars=list(series.candles)
    # Prior bars look like many touches near the final level, but the actual
    # final 20-bar extremum first appears on the LAST prior completed bar.
    # The old implementation counted 10 tests despite this late discovery.
    for i in range(len(bars)-23,len(bars)-3):
        if side=="BUYERS":
            bars[i]=replace(bars[i],low=100.2)
        else:
            bars[i]=replace(bars[i],high=109.8)
    if side=="BUYERS":
        bars[-4]=replace(bars[-4],low=100.)
    else:
        bars[-4]=replace(bars[-4],high=110.)
    series=replace(series,candles=tuple(bars))
    result=assess_levels_and_route(snapshot,instrument,series)
    level=next(x for x in result["level_candidates"] if x["type"]==level_kind)
    assert result["classification_status"]=="PROVISIONAL_ROUTE_CANDIDATE"
    assert level["strength"]=="WEAK"
    assert level["distinct_prior_test_count"]==0
    assert level["observed_test_history"]==[]
    assert level["price_first_observable_at_utc"]==(
        bars[-4].observed_at.replace("+00:00","Z"))
    assert level["price_first_observable_bar_end_utc"]==(
        bars[-4].end.replace("+00:00","Z"))
    assert level["level_detected_ex_ante_proven"] is False
    assert result["level_history_schema"]=="LEVEL_HISTORY_RESEARCH_V2"


def test_level_discovery_candle_not_counted_as_confirmation():
    snapshot,instrument,series=prepared("BUYERS")
    result=assess_levels_and_route(snapshot,instrument,series)
    level=next(x for x in result["level_candidates"]
               if x["type"]=="PRIOR_SUPPORT")
    # Discovery itself held above the eventual floor, but is not a retest.
    assert len(level["observed_test_history"])>0
    assert all(e["observed_end_utc"]!=level["price_first_observable_bar_end_utc"]
               for e in level["observed_test_history"])


def test_out_of_order_source_observations_cannot_certify_level_test_history():
    snapshot,instrument,series=prepared("BUYERS")
    bars=list(series.candles)
    # Both observations remain <= T0 and individually valid, but the
    # backfilled order no longer proves the chronological discovery path.
    bars[-5]=replace(bars[-5],observed_at=bars[-4].observed_at)
    result=assess_levels_and_route(
        snapshot,instrument,replace(series,candles=tuple(bars)))
    assert result["classification_status"]=="ABSTAIN"
    assert "LEVEL_HISTORY_EVENT_ORDER_UNVERIFIED" in result["reason_codes"]
    assert result["typed_obstacles_ahead"]==[]
    levels=[x for x in result["level_candidates"]
            if x["price_source"]=="COMPLETED_M15_ROLLING_EXTREME"]
    assert all(x["strength"]=="UNVERIFIED" for x in levels)
    assert all(x["distinct_prior_test_count"] is None for x in levels)
    assert all(x["price_first_observable_at_utc"] is None for x in levels)
    assert result["scenario_or_trade_authorized"] is False
