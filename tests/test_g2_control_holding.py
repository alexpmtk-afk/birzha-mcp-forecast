"""Causal Control/Holding distinct from aggregate Delta pressure and baseline trend."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from birzha.application.control_holding import control_holding_from_snapshot
from birzha.domain.market import Candle, CandleSeries
from test_market_state import _snapshot, _instrument, _flow

T0="2026-10-10T13:00:00Z"


def fixture(side="BUYERS", *, with_flow=True):
    instrument=_instrument("equity","CANDLES","VOLUME","TRADESTATS")
    first=datetime(2026,10,10,0,0,tzinfo=timezone.utc)
    bars=[]
    for i in range(50):
        if i<47:
            open_,high,low,close,volume=104.0,110.0,100.0,104.0,100.0
        elif side=="BUYERS":
            open_,high,low,close,volume=(
                (100.9,103.,100.,101.,150.),
                (101.,103.,100.8,101.3,120.),
                (101.3,103.,101.,101.5,120.),
            )[i-47]
        else:
            open_,high,low,close,volume=(
                (109.1,110.,107.,109.,150.),
                (109.,109.2,107.,108.7,120.),
                (108.7,109.,107.,108.5,120.),
            )[i-47]
        begin=first+timedelta(minutes=i*15)
        end=begin+timedelta(minutes=15)
        bars.append(Candle(
            open=open_,high=high,low=low,close=close,
            volume=volume,value=volume*close,
            begin=begin.isoformat(),end=end.isoformat(),
            completed=True,available_at=end.isoformat(),
            observed_at=(end+timedelta(minutes=1)).isoformat(),
            available_at_confidence="EXACT",source="SYNTHETIC",
        ))
    old=_snapshot()
    quality=replace(
        old.quality_contract,
        timeframes=tuple(replace(q,candles=50) if q.timeframe=="M15" else q
                         for q in old.quality_contract.timeframes)
    )
    flow=replace(_flow("PASS"),as_of=T0,volume_delta_ratio=-.5
                 if side=="BUYERS" else .5) if with_flow else None
    snapshot=replace(
        old,as_of=T0,source="SYNTHETIC_CONTROL_M15",
        m15=replace(old.m15,candles=50,last_close=bars[-1].close),
        normalized_features=replace(old.normalized_features,
                                    d1_atr_price_scale=2.0),
        quality_contract=quality,flow=flow,
    )
    return snapshot,instrument,CandleSeries(instrument=instrument,
                                            timeframe="M15",candles=tuple(bars))


@pytest.mark.parametrize("side,opposing",[
    ("BUYERS","SELLERS_FAVORED"),("SELLERS","BUYERS_FAVORED")
])
def test_reaction_and_two_holding_bars_provisionally_establish_side_independent_of_pressure(side,opposing):
    snap,ins,series=fixture(side)
    unchanged=snap.to_dict()
    observed=control_holding_from_snapshot(snap,ins,series)
    assert observed["classification_status"]=="PROVISIONAL_HOLDING_CANDIDATE"
    assert observed["control"]==side
    assert observed["control_confidence_out_of_10_not_probability"]>=6
    assert observed["pressure_separate_from_control"]["side"]==opposing
    assert observed["levels_from_previous_20_completed_bars"]=={
        "support":100.0,"resistance":110.0
    }
    assert observed["reaction_holding_evidence"]["holding_points"]==4
    assert observed["candle_activity"]["reaction_volume_over_previous_20_median"]==1.5
    assert observed["provider_source_independently_authenticated"] is False
    assert observed["holding_confidence_externally_calibrated"] is False
    assert observed["forecast_or_execution_authorized"] is False
    assert observed["production_authorized"] is False
    assert snap.to_dict()==unchanged


def test_flow_can_be_absent_without_fabricating_buyers_sellers_pressure():
    snap,ins,series=fixture(with_flow=False)
    result=control_holding_from_snapshot(snap,ins,series)
    assert result["control"]=="BUYERS"
    assert result["pressure_separate_from_control"]["side"] is None
    assert result["pressure_separate_from_control"]["status"]=="UNAVAILABLE"


@pytest.mark.parametrize("edit,code",[
    ("unknown_available_at","NONCAUSAL_OR_UNVERIFIED_M15_CANDLE"),
    ("future_observation","NONCAUSAL_OR_UNVERIFIED_M15_CANDLE"),
    ("partial_candle","NONCAUSAL_OR_UNVERIFIED_M15_CANDLE"),
    ("out_of_order","NONCAUSAL_OR_UNVERIFIED_M15_CANDLE"),
    ("bad_low_high","NONCAUSAL_OR_UNVERIFIED_M15_CANDLE"),
    ("zero_volume","NONCAUSAL_OR_UNVERIFIED_M15_CANDLE"),
    ("wrong_close","SNAPSHOT_LAST_CLOSE_DIFFERS_FROM_SOURCE"),
    ("wrong_secid","INSTRUMENT_OR_TIMEFRAME_MISMATCH"),
    ("wrong_board","INSTRUMENT_OR_TIMEFRAME_MISMATCH"),
    ("wrong_timeframe","INSTRUMENT_OR_TIMEFRAME_MISMATCH"),
    ("short_count","M15_COMPLETE_HISTORY_MISMATCH"),
    ("data_quality","DATA_QUALITY_NOT_PASS"),
    ("m15_quality","M15_QUALITY_CONTRACT_NOT_PASS"),
    ("no_volume_capability","VOLUME_CAPABILITY_MISSING"),
    ("no_atr","ATR_SCALE_UNAVAILABLE"),
])
def test_missing_unverified_or_inconsistent_source_prevents_control_candidate(edit,code):
    snap,ins,series=fixture()
    bars=list(series.candles)
    if edit=="unknown_available_at":
        bars[-1]=replace(bars[-1],available_at_confidence="UNKNOWN")
    if edit=="future_observation":
        bars[-1]=replace(bars[-1],observed_at="2026-10-11T00:00:00Z")
    if edit=="partial_candle":
        bars[-1]=replace(bars[-1],completed=False)
    if edit=="out_of_order":
        bars[-1]=replace(bars[-1],begin=bars[-2].begin)
    if edit=="bad_low_high":
        bars[-1]=replace(bars[-1],low=500)
    if edit=="zero_volume":
        bars[-1]=replace(bars[-1],volume=0)
    if edit=="wrong_close":
        snap=replace(snap,m15=replace(snap.m15,last_close=999))
    if edit=="wrong_secid":
        ins=replace(ins,secid="GAZP")
    if edit=="wrong_board":
        ins=replace(ins,board="OTHER")
    if edit=="wrong_timeframe":
        series=replace(series,timeframe="H1")
    if edit=="short_count":
        bars=bars[1:]
    if edit=="data_quality":
        snap=replace(snap,data_quality="DEGRADED")
    if edit=="m15_quality":
        snap=replace(snap,quality_contract=replace(
            snap.quality_contract,timeframes=tuple(
                replace(q,status="DEGRADED") if q.timeframe=="M15" else q
                for q in snap.quality_contract.timeframes)))
    if edit=="no_volume_capability":
        ins=replace(ins,data_capabilities=("CANDLES","TRADESTATS"))
    if edit=="no_atr":
        snap=replace(snap,normalized_features=replace(
            snap.normalized_features,d1_atr_price_scale=None))
    series=replace(series,candles=tuple(bars))
    result=control_holding_from_snapshot(snap,ins,series)
    assert result["classification_status"]=="ABSTAIN"
    assert result["control"] is None
    assert result["control_confidence_out_of_10_not_probability"] is None
    assert code in result["reason_codes"]


def test_flow_degraded_or_misaligned_never_sets_pressure_side():
    snap,ins,series=fixture()
    for bad in (
        replace(snap.flow,data_quality="DEGRADED"),
        replace(snap.flow,warnings=("ALGOPACK_TRADESTATS_UNAVAILABLE:timeout",)),
        replace(snap.flow,as_of="2026-10-09T13:00:00Z"),
        replace(snap.flow,secid="GAZP"),
    ):
        result=control_holding_from_snapshot(replace(snap,flow=bad),ins,series)
        assert result["pressure_separate_from_control"]["status"]=="UNAVAILABLE"
        assert result["pressure_separate_from_control"]["side"] is None


def test_price_direction_or_pressure_alone_do_not_create_holding():
    snap,ins,series=fixture()
    bars=list(series.candles)
    bars[-1]=replace(bars[-1],low=99.0,close=101.5)
    result=control_holding_from_snapshot(
        replace(snap,m15=replace(snap.m15,last_close=101.5)),
        ins,replace(series,candles=tuple(bars)),
    )
    assert result["classification_status"]=="ABSTAIN"
    assert "NO_CONFIRMED_REACTION_AND_TWO_HOLDS" in result["reason_codes"]
    assert result["pressure_separate_from_control"]["side"]=="SELLERS_FAVORED"


def test_last_three_are_reaction_and_holds_not_a_future_leakage_window():
    snap,ins,series=fixture()
    event=control_holding_from_snapshot(snap,ins,series)["reaction_holding_evidence"]
    assert event["reaction_end_utc"]=="2026-10-10T12:00:00Z"
    assert event["hold_ends_utc"]==[
        "2026-10-10T12:15:00Z","2026-10-10T12:30:00Z"
    ]
    assert "PROVISIONAL" in control_holding_from_snapshot(snap,ins,series)["classification_status"]


def test_no_source_manifest_proof_is_ever_assumed_from_synthetic_candles():
    snap,ins,series=fixture()
    result=control_holding_from_snapshot(snap,ins,series)
    assert result["provider_source_independently_authenticated"] is False
    assert result["alignment_or_route_approved"] is False
    assert result["production_authorized"] is False


def test_strict_types_reject_dict_lookalikes():
    snap,ins,series=fixture()
    with pytest.raises(TypeError):
        control_holding_from_snapshot(snap,ins,{"candles":series.candles})
