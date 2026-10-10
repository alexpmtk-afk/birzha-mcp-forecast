"""Synthetic regressions for the separate direct arithmetic report checker."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from test_d1_regime_experiment import synthetic_bundle, prepare, evaluate

spec = importlib.util.spec_from_file_location("direct_regime_control",
    Path(__file__).resolve().parents[1] / "scripts/g2_verify_regime_development_report.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


@pytest.fixture
def bundle():
    payload = synthetic_bundle()
    frozen = prepare(payload)
    report = json.dumps(evaluate(payload, frozen)).encode()
    return payload, frozen, report


def test_direct_arithmetic_matches_synthetic_pipeline(bundle):
    payload, frozen, report = bundle
    result = control.verify(*payload, frozen, report)
    assert result["checked_classifications"] == 660
    assert result["parameter_fields_checked"] == 55
    assert result["all_comparisons_match"] is True
    assert result["independent_human_acceptance"] is False


@pytest.mark.parametrize("defect", [
    "class", "annual", "baseline", "parameter", "order", "forecast",
    "selected", "excluded", "attempts", "transitions",
])
def test_direct_arithmetic_rejects_corrupted_reports(bundle, defect):
    payload, frozen, report = bundle
    data = deepcopy(json.loads(report))
    if defect == "class":
        data["attempts"][0]["result"]["results"][0]["regime"]["state"] = "TREND"
    elif defect == "annual":
        data["sensitivity"]["SBER"]["DT_MINUS"]["by_year"]["2022"]["disagreement_all"]["value"] = 0
    elif defect == "baseline":
        data["baseline_comparisons"]["Si"]["center_coverage"]["numerator"] += 1
    elif defect == "parameter":
        data["attempts"][1]["parameters"]["trend_min_abs_d20"] += .1
    elif defect == "order":
        data["attempts"][0]["result"]["results"].reverse()
    elif defect == "forecast":
        data["forecast_admission"] = True
    elif defect == "selected":
        data["proposed_candidate"] = "DT_MINUS"
    elif defect == "excluded":
        data["attempts"][0]["result"]["upstream_exclusion_count"] += 1
    elif defect == "attempts":
        data["attempts"].pop()
    elif defect == "transitions":
        data["attempts"][0]["summaries"]["BR"]["all"]["linked_transitions"] += 1
    with pytest.raises(ValueError):
        control.verify(*payload, frozen, json.dumps(data).encode())


def test_direct_decoder_refuses_duplicate_and_nonfinite():
    with pytest.raises(ValueError):
        control.decode(b'{"a":1,"a":2}')
    with pytest.raises(ValueError):
        control.decode(b'{"a":NaN}')
