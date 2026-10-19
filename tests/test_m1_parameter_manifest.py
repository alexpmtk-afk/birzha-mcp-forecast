from copy import deepcopy

from birzha.application.m1_parameter_manifest import (
    MODEL_ID, REQUIRED_FIELDS, audit_m1_parameter_manifest,
)


def _schema_fixture():
    # Synthetic schema fixture, NOT a calibrated, accepted or publishable profile.
    sample = {key: {"rule": "synthetic_test"} for key in REQUIRED_FIELDS}
    sample.update({
        "model_id": MODEL_ID, "model_version": "TEST_ONLY", "specification_version": "2.2",
        "instrument_scope": ["SBER", "IMOEX"], "fit_start": "2021-01-01",
        "fit_end": "2022-01-01", "input_hash": "TEST_ONLY_HASH", "calendar_version": "TEST",
        "warmup": 20, "trailing_window": 30, "swing_w": 2,
        "consolidation_windows": [10, 15], "ER_balance": 0.2,
        "range_balance_atr": 2.0, "ER_trend": 0.5, "displacement_min_atr": 1.0,
        "breakout_buffer_atr": 0.1, "confirm_bars": 2, "hold_bars": 2,
        "retest_zone_atr": 0.2, "reclaim_buffer_atr": 0.1, "chain_max_age": 50,
        "RVOL_min_count": 10, "level_merge_atr": 0.5, "target_band_quantile": 0.75,
        "min_reward_risk": 1.0,
    })
    return sample


def test_no_manifest_is_configuration_required_and_never_authorized():
    report = audit_m1_parameter_manifest(None)
    assert report["status"] == "CONFIG_REQUIRED"
    assert not report["forecast_allowed"]


def test_empty_manifest_reports_all_missing_fields():
    report = audit_m1_parameter_manifest({})
    assert len(report["issues"]) == len(REQUIRED_FIELDS)


def test_complete_outer_schema_requires_review_not_activation():
    report = audit_m1_parameter_manifest(_schema_fixture())
    assert report["issues"] == []
    assert report["status"] == "REVIEW_REQUIRED"
    assert not report["forecast_allowed"]


def test_never_accepts_legacy_model_or_spec_version():
    fixture = _schema_fixture()
    fixture["specification_version"] = "2.1"
    fixture["model_id"] = "M2_TREND_CONSENSUS"
    assert len(audit_m1_parameter_manifest(fixture)["issues"]) == 2


def test_missing_quantitative_parameter_fails_closed():
    fixture = _schema_fixture()
    fixture.pop("hold_bars")
    report = audit_m1_parameter_manifest(fixture)
    assert report["status"] == "CONFIG_REQUIRED"
    assert "hold_bars: missing" in report["issues"]


def test_rejects_bool_nonfinite_and_zero_in_positive_counts():
    for value in (True, 0, -1, 1.25, float("inf"), float("nan")):
        fixture = _schema_fixture()
        fixture["confirm_bars"] = value
        assert audit_m1_parameter_manifest(fixture)["status"] == "CONFIG_REQUIRED"


def test_rejects_invalid_er_nan():
    fixture = _schema_fixture()
    fixture["ER_trend"] = float("nan")
    assert "ER_trend: expected finite number in [0, 1]" in audit_m1_parameter_manifest(fixture)["issues"]


def test_rejects_unapproved_instrument_and_duplicated_symbol():
    fixture = _schema_fixture()
    fixture["instrument_scope"] = ["SBER", "SBER", "RANDOM"]
    assert audit_m1_parameter_manifest(fixture)["status"] == "CONFIG_REQUIRED"


def test_rejects_backwards_fit_dates_and_malformed_date():
    fixture = _schema_fixture()
    fixture["fit_end"] = "2020-01-01"
    assert "fit window: start must precede end" in audit_m1_parameter_manifest(fixture)["issues"]
    fixture["fit_end"] = "yesterday"
    assert "fit_end: expected ISO date" in audit_m1_parameter_manifest(fixture)["issues"]


def test_requires_explicit_rule_objects_not_placeholder_text():
    fixture = deepcopy(_schema_fixture())
    fixture["event_quantiles"] = "TBD"
    assert audit_m1_parameter_manifest(fixture)["status"] == "CONFIG_REQUIRED"
