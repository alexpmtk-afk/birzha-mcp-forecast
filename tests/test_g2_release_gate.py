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


def test_current_release_pins_include_actual_latest_colleague_decisions_and_issuer():
    policy = base()
    checked = validate(policy)
    assert set(checked["three_pinned_heads"]) == {147, 155, 156}
    assert policy["pr_dependencies"]["154"] == 151
    assert policy["pr_dependencies"]["155"] == 154
    assert policy["pr_dependencies"]["153"] == 152
    assert policy["pr_dependencies"]["156"] == 153
    assert policy["release_gates"]["code_three_line_compatibility"]["status"] == "PASS"
    assert policy["evidence"]["synthetic_three_line_ci"]["tests_passed"] == 999
    assert checked["release_allowed"] is False


def test_stale_production_branch_151_cannot_replace_current_155_release_pin():
    policy = base()
    policy["release_leaves"][1]["pr"] = 151
    policy["release_leaves"][1]["head"] = "0fdba4b057cee4f249751e2dcc7b9c6be1f9a0ae"
    policy["release_leaves"][1]["parent_pr"] = 149
    with pytest.raises(ValueError, match="heads moved"):
        validate(policy)


def test_latest_decision_chain_rewire_fails_closed():
    policy = base()
    policy["pr_dependencies"]["155"] = 151
    with pytest.raises(ValueError, match="reordered"):
        validate(policy)


def test_pinned_issuer_without_actual_user_production_approval_is_blocked():
    policy = base()
    policy["user_approved_main_merge"] = True
    policy["user_approved_home_deploy"] = True
    checked = validate(policy)
    assert checked["release_allowed"] is False
    assert any(x["gate"] == "live_forecast_issuance_after_source_receipt"
               for x in checked["blocking_requirements"])
