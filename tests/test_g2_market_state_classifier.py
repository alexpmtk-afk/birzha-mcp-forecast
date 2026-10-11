"""Six-state ex-ante Market State research classifier offline tests."""
from dataclasses import replace

import pytest

from birzha.application.market_state import build_market_state_vector,MarketStateService
from birzha.application.market_state_classifier import (
    classify_market_state_vector, classify_market_state_from_snapshot,
    REGIMES,
)
from birzha.domain.normalized_features import NormalizedFeatureSet
from test_market_state import _snapshot,_instrument,_with_flow,_flow


def _build(*, d1=.5,h1=.4,m15=.3,eff=.4,loc=.5,
           vol=1.0,hvol=1.0,delta=None, data_quality="PASS",
           d1_candles=60,h1_candles=60, capabilities=None, as_of=None):
    base=_snapshot(d1_candles=d1_candles,h1_candles=h1_candles)
    features=replace(
        base.normalized_features,
        d1_return_5_atr=d1,h1_return_5_atr=h1,m15_return_5_atr=m15,
        d1_efficiency_20=eff,h1_efficiency_20=eff,m15_efficiency_20=eff,
        d1_price_location_20=loc,h1_price_location_20=loc,
        m15_price_location_20=loc,
        d1_volume_ratio_20=vol,h1_volume_ratio_20=hvol,
        delta_volume_ratio=delta,
    )
    quality=replace(base.quality_contract,status=data_quality)
    base=replace(base, normalized_features=features, data_quality=data_quality,
                 quality_contract=quality,
                 as_of=as_of or base.as_of)
    if delta is not None:
        base=_with_flow(base,replace(_flow("PASS"),volume_delta_ratio=delta))
    capability_set=capabilities or ("CANDLES","VOLUME","TRADESTATS")
    instrument=_instrument("equity",*capability_set)
    vector=build_market_state_vector(base,instrument)
    return base,instrument,vector


def test_six_regime_names_are_versioned_and_scores_not_probabilities():
    _,_,v=_build()
    result=classify_market_state_vector(v)
    assert tuple(result["regime_scores_out_of_10_not_probabilities"])==REGIMES
    assert result["schema"]=="MARKET_STATE_RULES_RESEARCH_V1"
    assert result["classification_research_only"] is True
    assert result["control_and_route_established"] is False
    assert result["scenario_or_execution_authorized"] is False
    assert result["production_authorized"] is False
    assert v.classification_status=="NOT_CLASSIFIED"


@pytest.mark.parametrize("sign,expected",[(1,"TREND_UP"),(-1,"TREND_DOWN")])
def test_three_timeframe_trend_requires_quality_and_distinct_bearish_bullish_evidence(sign,expected):
    snap,instr,v=_build(d1=2*sign,h1=1.5*sign,m15=1.1*sign,
                        eff=.85,loc=.85 if sign>0 else .15)
    original=snap.to_dict()
    result=classify_market_state_vector(v)
    assert result["classification_status"]=="PROVISIONAL_CANDIDATE"
    assert result["candidate_regime"]==expected
    assert result["regime_scores_out_of_10_not_probabilities"][expected]>=6.5
    assert result["regime_scores_out_of_10_not_probabilities"]["EVENT"] is None
    assert snap.to_dict()==original
    assert classify_market_state_from_snapshot(snap,instr)==result


def test_balance_needs_low_displacement_and_low_efficiency():
    _,_,v=_build(d1=0,h1=0,m15=0,eff=.1,loc=.5)
    result=classify_market_state_vector(v)
    assert result["classification_status"]=="PROVISIONAL_CANDIDATE"
    assert result["candidate_regime"]=="BALANCE"
    assert result["regime_scores_out_of_10_not_probabilities"]["TREND_UP"] is None


@pytest.mark.parametrize("delta,d1,h1,m15,expected",[
    (-.5,.05,1.5,1.5,"ACCUMULATION"),
    (+.5,-.05,-1.5,-1.5,"DISTRIBUTION"),
])
def test_effort_result_states_are_optional_hypotheses_never_certified_holding(
        delta,d1,h1,m15,expected):
    _,_,v=_build(d1=d1,h1=h1,m15=m15,eff=.6,loc=.5,vol=4,delta=delta)
    result=classify_market_state_vector(v)
    assert result["candidate_regime"]==expected
    assert result["classification_status"]=="PROVISIONAL_CANDIDATE"
    assert result["control_and_route_established"] is False


def test_event_only_says_price_volume_anomaly_not_external_news_cause():
    _,_,v=_build(d1=2.2,h1=-.3,m15=-.4,eff=.3,loc=.5,vol=4,hvol=4)
    result=classify_market_state_vector(v)
    assert result["candidate_regime"]=="EVENT"
    assert "external_cause_not_inferred" in result["feature_evidence_by_regime"]["EVENT"]


@pytest.mark.parametrize("fault,expected",[
    ({"d1_candles":49},"TIMEFRAME_NOT_COMPLETE:D1"),
    ({"h1_candles":49},"TIMEFRAME_NOT_COMPLETE:H1"),
    ({"data_quality":"DEGRADED"},"SNAPSHOT_QUALITY_NOT_PASS"),
    ({"capabilities":("TRADING_CALENDAR",)},"CANDLES_NOT_SUPPORTED"),
    ({"as_of":"2026-10-01T18:00:00"},"TIME_WITHOUT_TRUSTWORTHY_OFFSET"),
    ({"d1":None},"CORE_FEATURE_UNAVAILABLE:d1_return_5_atr"),
    ({"loc":2.0},"INVALID_RANGE:d1_price_location_20"),
])
def test_bad_causal_input_or_clock_must_abstain(fault,expected):
    _,_,v=_build(**fault)
    result=classify_market_state_vector(v)
    assert result["classification_status"]=="ABSTAIN"
    assert result["candidate_regime"] is None
    assert expected in result["reason_codes"]
    assert all(x is None for x in result["regime_scores_out_of_10_not_probabilities"].values())


def test_nonfinite_normalized_value_is_refused_without_serializing_corrupted_snapshot():
    # The MarketSnapshot already forbids NaN in its stable content hash.
    # Direct forged/interrupted vectors must still fail closed at classifier.
    _,_,vector=_build()
    forged=replace(vector, features=replace(vector.features,d1_efficiency_20=float("nan")))
    result=classify_market_state_vector(forged)
    assert result["classification_status"]=="ABSTAIN"
    assert "INVALID_NUMERIC:d1_efficiency_20" in result["reason_codes"]


def test_conflicting_timeframes_without_support_abstains_not_force_direction():
    _,_,v=_build(d1=.8,h1=-.8,m15=.8,eff=.9,loc=.8)
    result=classify_market_state_vector(v)
    assert result["classification_status"]=="ABSTAIN"
    assert result["candidate_regime"] is None
    assert "INSUFFICIENT_SCORE_OR_MARGIN" in result["reason_codes"]


@pytest.mark.parametrize("d1,h1,m15,denied",[
    (.8,-.8,.8,"TREND_UP"),
    (-.8,.8,-.8,"TREND_DOWN"),
])
def test_material_opposing_h1_move_blocks_false_two_of_three_trend(
        d1,h1,m15,denied):
    _,_,v=_build(d1=d1,h1=h1,m15=m15,eff=.9,
                 loc=.8 if denied=="TREND_UP" else .2)
    result=classify_market_state_vector(v)
    assert result["candidate_regime"]!=denied
    assert result["regime_scores_out_of_10_not_probabilities"][denied] is None


def test_unavailable_optional_flow_does_not_mean_zero_delta_or_fake_absorption():
    _,_,v=_build(d1=.05,h1=1.5,m15=1.5,eff=.6,loc=.5,vol=4,
                 delta=None)
    result=classify_market_state_vector(v)
    assert result["regime_scores_out_of_10_not_probabilities"]["ACCUMULATION"] is None
    assert result["regime_scores_out_of_10_not_probabilities"]["DISTRIBUTION"] is None


def test_degraded_flow_is_masked_by_existing_vector_before_classifying_absorption():
    base,instr,v=_build(d1=.05,h1=1.5,m15=1.5,eff=.6,loc=.5,vol=4,delta=-.5)
    degraded=replace(
        base,flow=replace(_flow("DEGRADED",("ALGOPACK_TRADESTATS_UNAVAILABLE:timeout",)),
                          volume_delta_ratio=-.5),
    )
    v2=build_market_state_vector(degraded,instr)
    assert dict(v2.availability)["delta_volume_ratio"].status=="UNAVAILABLE"
    result=classify_market_state_vector(v2)
    assert result["regime_scores_out_of_10_not_probabilities"]["ACCUMULATION"] is None


def test_original_market_state_service_build_still_unclassified_and_new_optin_calls_once():
    base,instr,_=_build(d1=2,h1=1.5,m15=1.1,eff=.85,loc=.85)
    class Stub:
        calls=0
        def build_with_instrument(self,symbol,*,as_of_date=None):
            self.calls+=1
            assert symbol=="TEST"
            return base,instr
    svc=MarketStateService(Stub())
    old=svc.build("TEST")
    assert old["classification_status"]=="NOT_CLASSIFIED"
    reviewed=svc.classify("TEST")
    assert reviewed["classification_status"]=="PROVISIONAL_CANDIDATE"
    assert reviewed["snapshot_id"]==old["snapshot_id"]
    assert svc.snapshots.calls==2  # one per explicit invocation; no implicit extra reads


def test_classifier_refuses_forged_dictionary_not_grounded_vector():
    with pytest.raises(TypeError):
        classify_market_state_vector({"features":{"d1_return_5_atr":50}})
