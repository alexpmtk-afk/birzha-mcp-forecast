"""Wiring/safety checks only — NO forecast backtest or method-ranking tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta, datetime
import pytest

from birzha.application.forecast_research import ForecastResearchService, VERSION
from birzha.domain.market import CandleSeries
from test_g2_level_route import prepared


def test_single_snapshot_wires_all_existing_engines_and_preserves_inputs():
    snapshot, instrument, m15 = prepared("BUYERS")
    orig = snapshot.to_dict()
    series_orig = m15.to_dict()
    output = ForecastResearchService().build(snapshot, instrument, m15)
    assert output["schema"] == VERSION
    assert output["snapshot_id"] == output["market_state"]["snapshot_id"]
    assert output["snapshot_id"] == output["control_holding"]["snapshot_id"]
    assert output["snapshot_id"] == output["level_route"]["snapshot_id"]
    assert output["snapshot_id"] == output["alignment15"]["snapshot_id"]
    assert output["market_state"]["candidate_regime"] == "TREND_UP"
    assert output["control_holding"]["control"] == "BUYERS"
    assert output["level_route"]["route_direction"] == "UP"
    assert output["alignment15"]["selected_decision"] == "NO_TRADE"
    assert output["candidate_comparisons"]["models"]["STATE_CONTROL_ROUTE_CONSENSUS"]["operational_decision"] == "NOT_AUTHORIZED"
    assert output["benchmark_registry"][0]["id"] == "LEGACY_V04"
    assert output["benchmark_registry"][0]["called"] is False
    assert output["benchmark_registry"][1]["id"] == "LEGACY_V06"
    assert output["market_structure_facts"]["D1"]["status"] == "NOT_PROVIDED"
    assert output["operational_forecast"] is None
    assert output["outcomes"] is None
    assert output["model_performance"] == "NOT_EVALUATED_PENDING_CONSULTANT_REVIEW"
    assert output["original_provider_first_receipt_attested"] is False
    assert output["trade_or_execution_authorized"] is False
    assert snapshot.to_dict() == orig
    assert m15.to_dict() == series_orig
    assert ForecastResearchService().build(snapshot, instrument, m15) == output


@pytest.mark.parametrize("side,trend", [
    ("BUYERS", "TREND_UP"), ("SELLERS", "TREND_DOWN")
])
def test_signed_direction_comes_from_state_and_control_not_volume(side, trend):
    snap,inst,series = prepared(side)
    out = ForecastResearchService().build(snap,inst,series)
    factors = {x["family"]: x for x in out["factor_evidence"]}
    assert out["market_state"]["candidate_regime"] == trend
    assert factors["market_state"]["signed_strength"] == (1.0 if side == "BUYERS" else -1.0)
    assert factors["control"]["signed_strength"] == (1.0 if side == "BUYERS" else -1.0)
    assert factors["efficiency"]["signed_strength"] is None
    assert factors["effort_result"]["signed_strength"] is None
    assert factors["location"]["signed_strength"] is None
    assert factors["route"]["signed_strength"] == (1.0 if side == "BUYERS" else -1.0)
    assert out["candidate_comparisons"]["counted_vote_groups"] <= 3
    assert out["alignment15"]["selected_decision"] == "NO_TRADE"


def test_no_flow_cannot_become_pressure_agreement():
    snap,inst,series = prepared("BUYERS")
    snap = replace(snap,flow=None)
    out=ForecastResearchService().build(snap,inst,series)
    x = {v["family"]:v for v in out["factor_evidence"]}
    assert x["pressure"]["status"] == "UNAVAILABLE"
    assert x["pressure"]["signed_strength"] is None
    assert "FLOW" not in out["candidate_comparisons"]["group_votes"]


def test_unsafe_m15_source_forces_all_abstentions():
    snap,inst,series=prepared("BUYERS")
    end=series.candles[-1]
    broken=replace(end,observed_at="2026-10-18T12:00:00Z")
    series=replace(series,candles=(*series.candles[:-1],broken))
    out=ForecastResearchService().build(snap,inst,series)
    assert "NONCAUSAL_OR_UNVERIFIED_M15_CANDLE" in out["fatal_source_evidence"]
    assert all(v["candidate_direction"]=="ABSTAIN" for v in out["candidate_comparisons"]["models"].values())


def test_wrong_source_identity_rejected_without_fetching():
    snap,inst,series=prepared("BUYERS")
    broken=replace(series,instrument=replace(inst,secid="WRONG"))
    with pytest.raises(ValueError,match="exact SECID"):
        ForecastResearchService().build(snap,inst,broken)


def test_descriptive_structure_must_use_exact_source_and_tf():
    snap,inst,series=prepared("BUYERS")
    wrong=replace(series,timeframe="H1")
    with pytest.raises(ValueError,match="same exact contract"):
        ForecastResearchService().build(snap,inst,series,d1_series=wrong)


def test_suspicious_existing_proxy_profile_is_not_directional_vote():
    snap,inst,series=prepared("BUYERS")
    out=ForecastResearchService().build(snap,inst,series)
    flags={f["family"]:f for f in out["factor_evidence"]}
    assert flags["location"]["signed_strength"] is None
    assert out["alignment15"]["scorecards"]["LONG"]["factors"]["location"]["points"] is None
    assert out["alignment15"]["user_scoring_formula_approved"] is False


def test_quality_degraded_but_missing_flow_not_assumed_valid():
    snap,inst,series=prepared("BUYERS")
    degraded=replace(snap,data_quality="DEGRADED")
    out=ForecastResearchService().build(degraded,inst,series)
    assert "DATA_QUALITY_NOT_PASS" in out["fatal_source_evidence"]
    assert all(v["candidate_direction"]=="ABSTAIN" for v in out["candidate_comparisons"]["models"].values())


def test_full_forecast_module_does_not_auto_register_new_mcp_tool():
    from birzha.application import forecast_research
    assert not hasattr(forecast_research, "forecast_create")
    assert forecast_research.REGISTERED_VARIANTS[0]["called"] is False
