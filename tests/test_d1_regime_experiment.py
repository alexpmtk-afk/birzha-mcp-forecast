"""Synthetic tests of the unapproved eleven-candidate proposal simulation."""
from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import json

import pytest

from test_d1_research_dataset_adapter import encode, fixtures
from birzha.application.d1_research_dataset_adapter import MARKETS, canonical_json, sha256
from birzha.application.d1_regime_experiment import (
    balanced_quantile, candidate_specs, evaluate_frozen_candidates, prepare_quantile_candidates,
    _compare, _summarize,
)


def synthetic_bundle():
    manifest, originals, _ = fixtures()
    rows = []
    ds = (0., .5, 1., 1.5, 2., 3., 4., 5., 6., 7.)
    for market in MARKETS:
        original = next(r for r in originals if r["market"] == market)
        for i, d in enumerate(ds):
            row = deepcopy(original)
            dates = [(date(2021, 1, 1) + timedelta(days=i+j)).isoformat() for j in range(21)]
            row["expected_sessions"] = dates
            row["session"] = row["features"]["session"] = dates[-1]
            row["bar_event_end_at"] = dates[-1] + "T23:49:59"
            for name, value in (("d20_atr", d), ("er20", i / 10), ("w20_atr", i + 1)):
                row["features"]["features"][name]["value"] = value
            rows.append(row)
    manifest["accepted_rows"] = len(rows)
    manifest["excluded_rows"] = 0
    for market in MARKETS:
        manifest["per_market"][market] = {
            "development_active_candidates": 10, "admitted_reconstructed": 10,
            "excluded": 0, "exclusion_reasons": {},
        }
    return encode(manifest, rows, [])


def prepare(payload, percent=20):
    return prepare_quantile_candidates(
        *payload, trusted_manifest_sha256=sha256(payload[0]),
        experiment_id="synthetic-experiment", procedure_sha256="a" * 64,
        max_disagreement_percent=percent,
    )


def evaluate(payload, frozen, percent=20):
    return json.loads(evaluate_frozen_candidates(
        *payload, frozen, trusted_manifest_sha256=sha256(payload[0]),
        trusted_frozen_sha256=sha256(frozen), max_disagreement_percent=percent,
    ))


def test_equal_market_weights_are_not_equal_row_weights():
    values = {m: (0.,) for m in MARKETS}
    values["BR"] = (100.,) * 1000
    assert balanced_quantile(values, percent=75) == 0
    assert balanced_quantile(values, percent=83) == 0
    assert balanced_quantile(values, percent=84) == 100
    assert balanced_quantile(values, percent=100) == 100


def test_exact_cdf_boundary_and_ties():
    values = {m: (0., 1.) for m in MARKETS}
    assert balanced_quantile(values, percent=0) == 0
    assert balanced_quantile(values, percent=50) == 0
    assert balanced_quantile(values, percent=51) == 1
    assert balanced_quantile({m: (2., 2.) for m in MARKETS}, percent=75) == 2


@pytest.mark.parametrize("percent", [True, -1, 101, 75.0, None])
def test_quantile_percent_rejected(percent):
    with pytest.raises(ValueError):
        balanced_quantile({m: (1.,) for m in MARKETS}, percent=percent)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "1", 10 ** 400])
def test_quantile_bad_values(value):
    values = {m: (1.,) for m in MARKETS}
    values["BR"] = (value,)
    with pytest.raises(ValueError):
        balanced_quantile(values, percent=50)


def test_missing_or_empty_market_is_not_reweighted():
    values = {m: (1.,) for m in MARKETS}
    values.pop("BR")
    with pytest.raises(ValueError):
        balanced_quantile(values, percent=50)
    values["BR"] = ()
    with pytest.raises(ValueError):
        balanced_quantile(values, percent=50)


def test_specs_have_only_center_and_ten_single_coordinate_neighbors():
    specs = candidate_specs()
    assert len(specs) == 11
    assert len({name for name, _ in specs}) == 11
    for _, levels in specs[1:]:
        differences = [abs(a-b) for a,b in zip(specs[0][1], levels) if a != b]
        assert differences == [10]


def test_freeze_is_deterministic_and_does_not_evaluate(monkeypatch, tmp_path):
    import birzha.application.d1_regime_experiment as experiment
    monkeypatch.chdir(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("classification during candidate preparation")
    monkeypatch.setattr(experiment, "run_d1_descriptive_research", forbidden)
    payload = synthetic_bundle()
    first = prepare(payload)
    assert first == prepare(payload)
    frozen = json.loads(first)
    assert frozen["stage"] == "PARAMETERS_FROZEN"
    assert frozen["procedure_status"] == "UNAPPROVED_PROPOSAL_SIMULATION"
    assert len(frozen["candidates"]) == 11
    center = frozen["candidates"][0]
    assert center["status"] == "VALID"
    assert center["parameters"]["trend_min_abs_d20"] == 5
    assert center["parameters"]["balance_max_abs_d20"] == 1
    assert center["parameters"]["trend_min_er20"] == .7
    assert center["parameters"]["balance_max_er20"] == .2
    assert center["parameters"]["balance_max_w20"] == 5
    assert list(tmp_path.iterdir()) == []


def test_complete_synthetic_evaluation_records_all_attempts_and_sensitivity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = synthetic_bundle()
    frozen = prepare(payload)
    result = evaluate(payload, frozen)
    assert result == evaluate(payload, frozen)
    assert result["completion"] == "COMPLETE"
    assert len(result["attempts"]) == 11
    assert all(a["status"] == "COMPLETE" for a in result["attempts"])
    assert result["proposed_candidate"] == "CENTER"
    assert result["forecast_admission"] is False
    for market in MARKETS:
        stats = result["center_summaries"][market]
        assert stats["all"]["classes"] == {
            "BALANCE": 3, "UNKNOWN": 4, "TREND_UP": 3, "TREND_DOWN": 0,
        }
        assert stats["all"]["linked_transitions"] == 2
        assert [r["length"] for r in stats["all"]["runs"]] == [3, 4, 3]
        assert stats["by_year"]["2022"]["meaningful_coverage"]["value"] is None
        assert result["sensitivity"][market]["DT_PLUS"]["disagreement_all"] == {
            "numerator": 1, "denominator": 10, "value": .1,
        }
    assert list(tmp_path.iterdir()) == []


def test_invalid_center_retains_eleven_invalid_attempts_and_never_evaluates(monkeypatch):
    import birzha.application.d1_regime_experiment as experiment
    payload = encode(*fixtures())
    def forbidden(*args, **kwargs):
        pytest.fail("invalid CENTER should stop before classification")
    monkeypatch.setattr(experiment, "run_d1_descriptive_research", forbidden)
    result = evaluate(payload, prepare(payload))
    assert result["completion"] == "ABORTED"
    assert len(result["attempts"]) == 11
    assert all(a["status"] == "INVALID_PARAMETERS" for a in result["attempts"])
    assert result["proposed_candidate"] is None
    assert "INVALID_CENTER" in result["selection_reasons"]


def test_zero_width_candidate_is_invalid_not_repaired():
    manifest, rows, excluded = fixtures()
    for row in rows:
        row["features"]["features"]["w20_atr"]["value"] = 0
    frozen = json.loads(prepare(encode(manifest, rows, excluded)))
    assert all(c["status"] == "INVALID" for c in frozen["candidates"])
    assert all(c["parameters"]["balance_max_w20"] == 0 for c in frozen["candidates"])


def test_empty_market_stops_without_invented_parameters():
    manifest, rows, excluded = fixtures()
    rows = [r for r in rows if r["market"] != "BR"]
    manifest["accepted_rows"] -= 1
    manifest["per_market"]["BR"] = {
        "development_active_candidates": 0, "admitted_reconstructed": 0,
        "excluded": 0, "exclusion_reasons": {},
    }
    payload = encode(manifest, rows, excluded)
    frozen = prepare(payload)
    result = evaluate(payload, frozen)
    assert result["completion"] == "ABORTED"
    assert all(c["parameters"] is None for c in json.loads(frozen)["candidates"])


def test_mutated_frozen_bytes_fail_external_hash():
    payload = synthetic_bundle()
    frozen = prepare(payload)
    with pytest.raises(ValueError, match="SHA"):
        evaluate_frozen_candidates(
            *payload, frozen+b" ", trusted_manifest_sha256=sha256(payload[0]),
            trusted_frozen_sha256=sha256(frozen), max_disagreement_percent=20)


@pytest.mark.parametrize("field,value", [
    ("budget", 10), ("budget", True), ("stage", "COMPLETE"),
    ("feature_version", "wrong"), ("trusted_manifest_sha256", "b"*64),
    ("regime_version", "wrong"), ("procedure_status", "APPROVED"),
])
def test_frozen_contract_rejected(field, value):
    payload = synthetic_bundle()
    frozen = json.loads(prepare(payload))
    frozen[field] = value
    with pytest.raises(ValueError):
        evaluate(payload, canonical_json(frozen).encode())


@pytest.mark.parametrize("mutation", ["fingerprint", "order", "count", "levels", "missing_parameters"])
def test_candidate_integrity(mutation):
    payload = synthetic_bundle()
    frozen = json.loads(prepare(payload))
    if mutation == "fingerprint":
        frozen["candidates"][0]["parameter_fingerprint"] = "b"*64
    elif mutation == "order":
        frozen["candidates"].reverse()
    elif mutation == "count":
        frozen["candidates"].pop()
    elif mutation == "levels":
        frozen["candidates"][0]["quantile_levels"][0] = 80
    else:
        frozen["candidates"][0]["parameters"] = {}
    with pytest.raises((ValueError, TypeError)):
        evaluate(payload, canonical_json(frozen).encode())


def test_evaluation_failure_is_aborted_with_failed_and_unrun_attempts(monkeypatch):
    import birzha.application.d1_regime_experiment as experiment
    payload = synthetic_bundle()
    original = experiment.run_d1_descriptive_research
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("synthetic failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(experiment, "run_d1_descriptive_research", fail_second)
    result = evaluate(payload, prepare(payload))
    assert result["completion"] == "ABORTED"
    assert result["proposed_candidate"] is None
    assert result["attempts"][0]["status"] == "COMPLETE"
    assert result["attempts"][1]["status"] == "FAILED"
    assert all(a["status"] == "NOT_RUN" for a in result["attempts"][2:])
    assert len(calls) == 2


def test_sensitive_center_does_not_select_another_candidate():
    payload = synthetic_bundle()
    result = evaluate(payload, prepare(payload, percent=0), percent=0)
    assert result["proposed_candidate"] is None
    assert any(r.startswith("SENSITIVE:") for r in result["selection_reasons"])


def test_degenerate_center_never_automatically_selected():
    payload = synthetic_bundle()
    manifest, rows, excluded = fixtures()
    # SBER constant large D/high ER; other markets provide valid global quantiles.
    synthetic_manifest, synthetic_rows, _ = [json.loads(payload[0]),
        [json.loads(line) for line in payload[1].splitlines()], []]
    for row in synthetic_rows:
        if row["market"] == "SBER":
            row["features"]["features"]["d20_atr"]["value"] = 100
            row["features"]["features"]["er20"]["value"] = 1
    payload = encode(synthetic_manifest, synthetic_rows, [])
    result = evaluate(payload, prepare(payload, percent=100), percent=100)
    assert result["completion"] == "COMPLETE"
    assert result["proposed_candidate"] is None
    assert "DEGENERATE_CENTER:SBER" in result["selection_reasons"]


def test_comparison_empty_meaningful_union_remains_null():
    stats = _compare(["UNKNOWN"]*3, ["UNKNOWN"]*3)
    assert stats["disagreement_all"]["value"] == 0
    assert stats["disagreement_meaningful_union"]["value"] is None


def test_runs_split_at_calendar_gap_and_contract_change():
    payload = synthetic_bundle()
    result = evaluate(payload, prepare(payload))
    rows = result["attempts"][0]["result"]["results"][:3]
    assert _summarize(rows)["runs"] == [{"label": "BALANCE", "length": 3}]
    rows = deepcopy(rows)
    rows[1]["input"]["secid"] = "OTHER"
    assert [r["length"] for r in _summarize(rows)["runs"]] == [1, 1, 1]
    rows = deepcopy(result["attempts"][0]["result"]["results"][:3])
    rows[1]["input"]["expected_sessions"][0] = "2020-12-31"
    assert _summarize(rows)["runs"][0]["length"] == 1


def test_threshold_has_no_default_and_bool_is_rejected():
    payload = synthetic_bundle()
    frozen = prepare(payload)
    kwargs = dict(trusted_manifest_sha256=sha256(payload[0]), trusted_frozen_sha256=sha256(frozen))
    with pytest.raises(TypeError):
        evaluate_frozen_candidates(*payload, frozen, **kwargs)
    with pytest.raises(ValueError):
        evaluate_frozen_candidates(*payload, frozen, **kwargs, max_disagreement_percent=True)


def test_frozen_disagreement_criterion_cannot_change_during_evaluation():
    payload = synthetic_bundle()
    with pytest.raises(ValueError, match="frozen procedure"):
        evaluate(payload, prepare(payload), percent=0)


def test_all_unknown_center_does_not_pass_via_zero_disagreement():
    payload = synthetic_bundle()
    manifest = json.loads(payload[0])
    rows = [json.loads(line) for line in payload[1].splitlines()]
    for i, row in enumerate(rows):
        row["features"]["features"]["er20"]["value"] = (9-i%10)/10
    payload = encode(manifest, rows, [])
    result = evaluate(payload, prepare(payload, percent=100), percent=100)
    assert result["completion"] == "COMPLETE"
    assert result["proposed_candidate"] is None
    assert all("DEGENERATE_CENTER:" + m in result["selection_reasons"] for m in MARKETS)
    assert all(s["all"]["classes"]["UNKNOWN"] == 10 for s in result["center_summaries"].values())


def test_invalid_neighbor_is_recorded_and_blocks_center_selection():
    from birzha.application.d1_research_regime import RegimeParameters
    payload = synthetic_bundle()
    frozen = json.loads(prepare(payload))
    neighbor = frozen["candidates"][1]
    neighbor["parameters"]["trend_min_abs_d20"] = neighbor["parameters"]["balance_max_abs_d20"]
    with pytest.raises(ValueError) as failure:
        RegimeParameters(**neighbor["parameters"])
    neighbor.update(status="INVALID", parameter_fingerprint=None, invalid_reason=str(failure.value))
    result = evaluate(payload, canonical_json(frozen).encode())
    assert result["completion"] == "COMPLETE"
    assert result["attempts"][1]["status"] == "INVALID_PARAMETERS"
    assert result["proposed_candidate"] is None
    assert "INVALID_CANDIDATES" in result["selection_reasons"]


def test_exactly_eleven_calls_no_optimization_or_extra_search(monkeypatch):
    import birzha.application.d1_regime_experiment as experiment
    payload = synthetic_bundle()
    original = experiment.run_d1_descriptive_research
    seen = []
    def counting(*args, **kwargs):
        seen.append(kwargs["parameters"].hypothesis_id)
        return original(*args, **kwargs)
    monkeypatch.setattr(experiment, "run_d1_descriptive_research", counting)
    evaluate(payload, prepare(payload))
    assert len(seen) == len(set(seen)) == 11

def test_yearly_comparisons_partition_without_changing_selection():
    original = synthetic_bundle()
    original_result = evaluate(original, prepare(original))
    manifest = json.loads(original[0])
    rows = [json.loads(line) for line in original[1].splitlines()]
    for index, row in enumerate(rows):
        if index % 10 >= 5:
            row["expected_sessions"] = [d.replace("2021", "2022") for d in row["expected_sessions"]]
            row["session"] = row["features"]["session"] = row["session"].replace("2021", "2022")
            row["bar_event_end_at"] = row["bar_event_end_at"].replace("2021", "2022")
    payload = encode(manifest, rows, [])
    result = evaluate(payload, prepare(payload))
    assert result["proposed_candidate"] == original_result["proposed_candidate"]
    assert result["selection_reasons"] == original_result["selection_reasons"]
    for market in MARKETS:
        for stats in [*result["sensitivity"][market].values(), result["baseline_comparisons"][market]]:
            annual = [stats["by_year"][y] for y in ("2021", "2022")]
            assert all(s["disagreement_all"]["denominator"] == 5 for s in annual)
            for key in ("disagreement_all", "disagreement_meaningful_union",
                        "center_coverage", "other_coverage"):
                for field in ("numerator", "denominator"):
                    assert sum(s[key][field] for s in annual) == stats[key][field]


def test_empty_year_comparison_denominators_remain_null():
    payload = synthetic_bundle()
    result = evaluate(payload, prepare(payload))
    for market in MARKETS:
        for stats in [*result["sensitivity"][market].values(), result["baseline_comparisons"][market]]:
            absent = stats["by_year"]["2022"]
            assert absent["pairs"] == {}
            for key in ("disagreement_all", "disagreement_meaningful_union",
                        "center_coverage", "other_coverage"):
                assert absent[key] == {"numerator": 0, "denominator": 0, "value": None}
