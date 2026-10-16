"""Regression gate for immutable Forecast V2 horizon schema.

This intentionally reuses the existing baseline evidence contract from #154.
No production data, market connection, or forecast recalibration is involved.
"""

import math

import pytest

from test_production_feature_contracts import snapshot
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.domain.forecast import validate_baseline_evidence_payload


def baseline_payload():
    return build_forecast_from_snapshot(snapshot(60)).to_dict()


@pytest.mark.parametrize("extra", [None, 17, "invalid", [], True])
def test_extra_non_object_horizon_is_controlled_rejection(extra):
    payload = baseline_payload()
    payload["horizons"].append(extra)
    with pytest.raises(ValueError, match="three ordered 5/10/20"):
        validate_baseline_evidence_payload(payload)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), "0.5", True, {}, []])
@pytest.mark.parametrize("field", ["expected_move_pct", "adverse_move_pct"])
def test_horizon_measure_must_be_finite_numeric_or_unknown(field, bad):
    payload = baseline_payload()
    payload["horizons"][1][field] = bad
    with pytest.raises(ValueError, match="invalid baseline horizon"):
        validate_baseline_evidence_payload(payload)


def test_reject_missing_horizon_and_accept_unmodified_record():
    payload = baseline_payload()
    validate_baseline_evidence_payload(payload)
    payload["horizons"].pop()
    with pytest.raises(ValueError, match="three ordered 5/10/20"):
        validate_baseline_evidence_payload(payload)


def test_legacy_record_contract_unchanged_by_v2_guard():
    from dataclasses import replace
    old = snapshot(60)
    old = replace(old, normalized_features=replace(old.normalized_features, version="NORMALIZED_FEATURES_V1"))
    old_payload = build_forecast_from_snapshot(old).to_dict()
    assert "decision_status" not in old_payload
    assert old_payload["record_version"] == "FORECAST_RECORD_V1_PROTOCOL_08"
    assert all(h["sessions"] in (5, 10, 20) for h in old_payload["horizons"])


def test_v2_preserves_ordered_horizons_and_finite_existing_outputs():
    record = build_forecast_from_snapshot(snapshot(60))
    payload = record.to_dict()
    assert [h["sessions"] for h in payload["horizons"]] == [5, 10, 20]
    for horizon in payload["horizons"]:
        for field in ("expected_move_pct", "adverse_move_pct"):
            value = horizon[field]
            assert value is None or (type(value) in (int, float) and math.isfinite(value))
    validate_baseline_evidence_payload(payload)
