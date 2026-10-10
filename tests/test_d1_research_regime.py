"""Synthetic hypotheses only: no archive, network, or threshold calibration."""
from dataclasses import replace
from datetime import date, timedelta
import json

import pytest

from birzha.application.d1_research_features import (
    D1ResearchFeatureSet, ResearchFeature, build_d1_research_features,
)
from birzha.application.d1_research_regime import (
    RegimeParameters, classify_d1_research_regime,
)
from birzha.domain.market import Candle, CandleSeries, Instrument


def params(**overrides):
    # Test-only values chosen to exercise branches, never a recommended preset.
    fields = dict(hypothesis_id="synthetic-test-only",
                  trend_min_abs_d20=3., trend_min_er20=.6,
                  balance_max_abs_d20=1., balance_max_er20=.2,
                  balance_max_w20=4.)
    return RegimeParameters(**(fields | overrides))


def inputs(d=5., er=.8, w=6., atr=2.):
    return D1ResearchFeatureSet(
        secid="TEST", session="2022-01-31",
        evidence_origin="RECONSTRUCTED_MOEX", evidence_key="synthetic",
        observed_bars=21,
        features=tuple((name, ResearchFeature(value, "AVAILABLE"))
                       for name, value in (
                           ("atr14_sma_tr", atr), ("d20_atr", d),
                           ("er20", er), ("w20_atr", w))),
    )


def classify(data=None, **overrides):
    return classify_d1_research_regime(
        data or inputs(), parameters=params(**overrides))


@pytest.mark.parametrize("d,er,w,state,direction", [
    (3., .6, 8., "TREND", "UP"), (-3., .6, 8., "TREND", "DOWN"),
    (1., .2, 4., "BALANCE", None), (-1., .2, 4., "BALANCE", None),
    (0., 0., 0., "BALANCE", None),
    (2., .4, 5., "UNKNOWN", None), (3., .599, 8., "UNKNOWN", None),
    (2.999, .6, 8., "UNKNOWN", None),
    (1.001, .2, 4., "UNKNOWN", None), (1., .201, 4., "UNKNOWN", None),
    (1., .2, 4.001, "UNKNOWN", None),
    (0., .8, 8., "UNKNOWN", None),
])
def test_rules_boundaries_and_direction(d, er, w, state, direction):
    result = classify(inputs(d, er, w))
    assert (result.state, result.direction) == (state, direction)
    assert len(result.rules) == 5
    assert result.strict_historical_as_known_at_t0 is False


@pytest.mark.parametrize("field,value", [
    ("trend_min_abs_d20", 1.), ("trend_min_abs_d20", 0.),
    ("balance_max_abs_d20", -1.), ("balance_max_er20", .6),
    ("balance_max_er20", -.1), ("trend_min_er20", 1.1),
    ("balance_max_w20", 0.), ("balance_max_w20", -1.),
    ("trend_min_er20", float("nan")), ("balance_max_w20", float("inf")),
    ("trend_min_abs_d20", True), ("balance_max_w20", "4"),
    ("hypothesis_id", ""), ("hypothesis_id", "  "),
])
def test_invalid_parameters_rejected(field, value):
    with pytest.raises(ValueError):
        params(**{field: value})


def test_no_default_thresholds():
    with pytest.raises(TypeError):
        RegimeParameters()


@pytest.mark.parametrize("name", [
    "atr14_sma_tr", "d20_atr", "er20", "w20_atr",
])
@pytest.mark.parametrize("status", [
    "UNAVAILABLE", "INSUFFICIENT_HISTORY", "NOT_APPLICABLE",
])
def test_required_status_fails_closed(name, status):
    data = inputs()
    data = replace(data, features=tuple(
        (key, ResearchFeature(None, status)) if key == name else (key, item)
        for key, item in data.features))
    result = classify(data)
    assert result.state == "UNKNOWN"
    assert result.rules == ()
    assert any(name in reason for reason in result.reasons)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), True, "1"])
def test_invalid_required_number(value):
    data = inputs()
    data = replace(data, features=tuple(
        (key, replace(item, value=value)) if key == "d20_atr" else (key, item)
        for key, item in data.features))
    assert classify(data).state == "UNKNOWN"


@pytest.mark.parametrize("d,er,w,atr,reason", [
    (5., -.1, 6., 2., "ER20_OUT_OF_RANGE"),
    (5., 1.1, 6., 2., "ER20_OUT_OF_RANGE"),
    (5., .8, -1., 2., "NEGATIVE_WIDTH"),
    (5., .8, 6., 0., "NONPOSITIVE_ATR"),
])
def test_numeric_domain_checks(d, er, w, atr, reason):
    result = classify(inputs(d, er, w, atr))
    assert result.state == "UNKNOWN"
    assert reason in result.reasons


@pytest.mark.parametrize("field,value,reason", [
    ("version", "wrong", "FEATURE_VERSION_MISMATCH"),
    ("atr_method", "WILDER", "ATR_METHOD_MISMATCH"),
    ("eligibility", "STRICT", "UNSUPPORTED_EVIDENCE_SCOPE"),
    ("strict_historical_as_known_at_t0", True, "UNSUPPORTED_STRICT_CAUSAL_CLAIM"),
    ("evidence_key", "", "MISSING_OR_UNSUPPORTED_SOURCE_IDENTITY"),
    ("secid", "", "MISSING_OR_UNSUPPORTED_SOURCE_IDENTITY"),
    ("evidence_origin", "invented", "MISSING_OR_UNSUPPORTED_SOURCE_IDENTITY"),
    ("session", "not-a-date", "INVALID_SESSION"),
    ("observed_bars", 20, "INSUFFICIENT_EXACT_SESSION_HISTORY"),
    ("observed_bars", True, "INSUFFICIENT_EXACT_SESSION_HISTORY"),
])
def test_input_contract_checks(field, value, reason):
    result = classify(replace(inputs(), **{field: value}))
    assert result.state == "UNKNOWN"
    assert reason in result.reasons


def test_duplicates_and_missing_features():
    data = inputs()
    assert "DUPLICATE_FEATURE_NAMES" in classify(
        replace(data, features=data.features + data.features[:1])).reasons
    assert "MISSING_FEATURE:atr14_sma_tr" in classify(
        replace(data, features=data.features[1:])).reasons


def test_optional_features_do_not_block_indices():
    data = inputs()
    data = replace(data, features=data.features + (
        ("volume_ratio20", ResearchFeature(None, "NOT_APPLICABLE")),
        ("profile_poc", ResearchFeature(None, "UNAVAILABLE")),
    ))
    assert classify(data).state == "TREND"


def test_serialization_reproducibility_and_parameter_identity():
    data = inputs()
    result = classify(data)
    encoded = json.dumps(result.to_dict(), sort_keys=True, allow_nan=False)
    assert encoded == json.dumps(classify(data).to_dict(), sort_keys=True)
    assert result == classify(replace(data, features=tuple(reversed(data.features))))
    assert params(trend_min_abs_d20=3).fingerprint == params().fingerprint
    assert params(hypothesis_id="other").fingerprint != params().fingerprint
    assert params(trend_min_abs_d20=4).fingerprint != params().fingerprint
    payload = result.to_dict()
    assert payload["parameter_status"] == "UNCALIBRATED_HYPOTHESIS"
    assert "probability" not in payload
    assert "confidence" not in payload


@pytest.mark.parametrize("step,expected", [(1., "UP"), (-1., "DOWN")])
def test_real_feature_builder_synthetic_price_sequence(step, expected):
    days = tuple((date(2022, 1, 1) + timedelta(days=i)).isoformat()
                 for i in range(21))  # Synthetic calendar, not exchange evidence.
    inst = Instrument(symbol="IMOEX", secid="IMOEX", board="SNDX",
                      engine="stock", market="index", asset_class="index",
                      data_capabilities=("CANDLES",))
    bars = tuple(Candle(open=100.+step*i, close=100.+step*i,
                        high=102.+step*i, low=98.+step*i, volume=0., value=0.,
                        begin=day+"T00:00:00", end=day+"T23:59:59",
                        completed=True, source="SYNTHETIC")
                 for i, day in enumerate(days))
    data = build_d1_research_features(
        CandleSeries(inst, "D1", bars), exact_secid="IMOEX",
        expected_sessions=days, evidence_origin="RECONSTRUCTED_MOEX",
        evidence_key="synthetic")
    result = classify(data)
    assert (result.state, result.direction) == ("TREND", expected)


def test_flat_price_path_unavailable_er_stays_unknown():
    data = inputs(d=0., w=1.)
    data = replace(data, features=tuple(
        (key, ResearchFeature(None, "UNAVAILABLE", "zero_20_step_closing_path"))
        if key == "er20" else (key, item) for key, item in data.features))
    assert classify(data).state == "UNKNOWN"


def test_balance_from_synthetic_oscillating_prices():
    days = tuple((date(2022, 1, 1) + timedelta(days=i)).isoformat()
                 for i in range(21))
    inst = Instrument(symbol="SBER", secid="SBER", board="TQBR",
                      engine="stock", market="shares", asset_class="equity",
                      data_capabilities=("CANDLES",))
    bars = tuple(Candle(open=100.+i%2, close=100.+i%2,
                        high=101.+i%2, low=99.+i%2,
                        volume=0., value=0., begin=day+"T00:00:00",
                        end=day+"T23:59:59", completed=True, source="SYNTHETIC")
                 for i, day in enumerate(days))
    data = build_d1_research_features(
        CandleSeries(inst, "D1", bars), exact_secid="SBER",
        expected_sessions=days, evidence_origin="RECONSTRUCTED_MOEX",
        evidence_key="synthetic")
    before = data.to_dict()
    result = classify(data)
    assert result.state == "BALANCE"
    assert result.direction is None
    assert data.to_dict() == before


def test_scale_invariant_when_normalized_features_unchanged():
    data = inputs()
    scaled = replace(data, features=tuple(
        (name, replace(item, value=item.value * 100))
        if name == "atr14_sma_tr" else (name, item)
        for name, item in data.features))
    assert classify(data).state == classify(scaled).state == "TREND"
    assert classify(data).rules == classify(scaled).rules


@pytest.mark.parametrize("reason", [
    "zero_20_step_closing_path",
    "invalid_or_unfinished_exact_session_ohlc",
    "requires_21_exact_sessions_got_20",
])
def test_unavailable_feature_preserves_upstream_reason(reason):
    data = inputs()
    data = replace(data, features=tuple(
        (key, ResearchFeature(None, "UNAVAILABLE", reason))
        if key == "er20" else (key, item) for key, item in data.features))
    result = classify(data)
    assert result.state == "UNKNOWN"
    assert result.rules == ()
    assert "UNAVAILABLE_FEATURE:er20:UNAVAILABLE" in result.reasons
    assert f"FEATURE_REASON:er20:{reason}" in result.to_dict()["reasons"]
