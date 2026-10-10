"""Fail-closed tests for the October 10 G2 no-production release decision."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.g2_release_gate import REQUIRED, validate, main


POLICY = Path(__file__).resolve().parents[1] / "docs/G2_RELEASE_GATE_20261010.json"


def base():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def test_actual_policy_is_blocked_even_when_some_ci_green():
    record = validate(base())
    assert record["release_state"] == "RELEASE_BLOCKED"
    assert record["release_allowed"] is False
    assert len(record["three_pinned_heads"]) == 3
    assert record["gates_total"] == len(REQUIRED)
    assert any(x["gate"] == "future_outcomes_5_10_20" for x in record["blocking_requirements"])
    assert any(x["gate"] == "user_approved_main_merge" for x in record["blocking_requirements"])


def test_ci_pass_alone_cannot_trigger_production_release():
    policy = base()
    policy["release_gates"]["code_three_line_compatibility"]["status"] = "PASS"
    assert validate(policy)["release_allowed"] is False
    policy["release_authorized"] = True
    with pytest.raises(ValueError, match="release_authorized contradicts"):
        validate(policy)


def test_both_explicit_permissions_do_not_override_failed_quality_gates():
    policy = base()
    policy["user_approved_main_merge"] = True
    policy["user_approved_home_deploy"] = True
    assert validate(policy)["release_allowed"] is False
    assert not any(x["gate"].startswith("user_approved") for x in validate(policy)["blocking_requirements"])


@pytest.mark.parametrize("missing", [
    "home_windows_readonly_acceptance",
    "historical_first_receipt_attestation",
    "forecast_protocol08_complete",
])
def test_required_gate_cannot_be_omitted(missing):
    policy = base()
    del policy["release_gates"][missing]
    with pytest.raises(ValueError, match="mandatory release gates"):
        validate(policy)


def test_pr_dependency_rewire_fails_closed():
    policy = base()
    policy["pr_dependencies"]["151"] = 148
    with pytest.raises(ValueError, match="reordered"):
        validate(policy)


def test_pin_move_requires_new_audit():
    policy = base()
    policy["release_leaves"][1]["head"] = "0" * 40
    with pytest.raises(ValueError, match="heads moved"):
        validate(policy)


def test_production_or_holdout_mutation_is_rejected():
    policy = base()
    policy["production_data_modified"] = True
    with pytest.raises(ValueError, match="production data"):
        validate(policy)
    policy = base()
    policy["holdout_2023_2024_opened"] = True
    with pytest.raises(ValueError, match="holdout"):
        validate(policy)


def test_required_explicit_release_command_exits_nonzero_for_blocked_policy():
    with pytest.raises(SystemExit) as exc:
        main(["--manifest", str(POLICY), "--require-release"])
    assert exc.value.code == 3


def test_reviewer_approval_is_not_inferred_from_green_tests():
    policy = base()
    for gate in policy["release_gates"].values():
        if gate["status"] != "PASS":
            gate["status"] = "PASS"
    assert validate(policy)["release_allowed"] is False
    assert any(x["gate"] == "user_approved_home_deploy" for x in validate(policy)["blocking_requirements"])


def test_latest_source_and_real_six_market_release_pins_are_exact():
    policy = base()
    heads = {p["pr"]: p["head"] for p in policy["release_leaves"]}
    assert set(heads) == {168, 169, 147}
    policy["release_leaves"][1]["pr"] = 167
    policy["release_leaves"][1]["head"] = "c5b815dd59dd226caf288aeb387b172911fefd4c"
    policy["release_leaves"][1]["parent_pr"] = 165
    with pytest.raises(ValueError, match="heads moved"):
        validate(policy)


@pytest.mark.parametrize("node,wrong_parent", [("168", 164), ("169", 165)])
def test_latest_stack_dependency_rewire_fails_closed(node, wrong_parent):
    policy = base()
    policy["pr_dependencies"][node] = wrong_parent
    with pytest.raises(ValueError, match="reordered"):
        validate(policy)


def test_wrong_parent_of_latest_leaf_fails_even_when_heads_match():
    policy = base()
    policy["release_leaves"][1]["parent_pr"] = 165
    with pytest.raises(ValueError, match="dependency not pinned"):
        validate(policy)
