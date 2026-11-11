import importlib.util
from pathlib import Path
import pytest

p = Path(__file__).resolve().parents[1] / ".agents/skills/birzha-chat-controller/scripts/control_gate.py"
spec = importlib.util.spec_from_file_location("control_gate", p)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

def state(**updates):
    x = dict(enabled=True, paused=False, pending_request=None, new_completed_result=True,
             consecutive_no_progress=0, all_requirements_verified=False,
             blocker_requires_owner=False, evidence_refs=[], replan=None, initial_request=False, cycle_id="cycle-3")
    x.update(updates)
    return x

@pytest.mark.parametrize("updates,expected", [
    ({"paused": True, "pending_request": {"marker": "x"}}, "PAUSED"),
    ({"enabled": False}, "PAUSED"),
    ({"pending_request": {"dispatch_confirmed": True, "message_id": None}}, "WAIT"),
    ({"pending_request": {}}, "WAIT"),
    ({"new_completed_result": False}, "NO_NEW_RESULT"),
    ({"consecutive_no_progress": 2}, "REPLAN"),
    ({"consecutive_no_progress": 9, "replan": {"hypothesis": "x"}}, "INVALID_STATE"),
    ({"blocker_requires_owner": True}, "BLOCKED"),
    ({"all_requirements_verified": True}, "INVALID_STATE"),
    ({"all_requirements_verified": True, "evidence_refs": ["verified/readback"]}, "COMPLETE"),
    ({"all_requirements_verified": False, "evidence_refs": ["partial"]}, "REVIEW"),
    ({"consecutive_no_progress": -1}, "INVALID_STATE"),
    ({"consecutive_no_progress": True}, "INVALID_STATE"),
    ({"paused": "false"}, "INVALID_STATE"),
    ({"evidence_refs": [""]}, "INVALID_STATE"),
    ({"pending_request": "unknown"}, "INVALID_STATE"),
    ({"consecutive_no_progress": 2, "replan": dict(changed_factor="environment", hypothesis="DNS",
       bounded_test="one request", expected_evidence="raw response", for_cycle="cycle-3")}, "REVIEW"),
])
def test_decision(updates, expected):
    assert gate.decide(state(**updates)) == expected

def test_resume_does_not_reset_failure_count():
    import json
    recovered = json.loads(json.dumps(state(consecutive_no_progress=2)))
    assert gate.decide(recovered) == "REPLAN"

def test_malformed():
    assert gate.decide([]) == "INVALID_STATE"
    assert gate.decide({}) == "INVALID_STATE"

def test_first_request():
    assert gate.decide(state(initial_request=True, new_completed_result=False)) == "START_REVIEW"
    assert gate.decide(state(initial_request=True)) == "INVALID_STATE"

def test_reused_plan_cannot_unlock_next_cycle():
    plan = dict(changed_factor="env", hypothesis="DNS", bounded_test="one", expected_evidence="response", for_cycle="old")
    assert gate.decide(state(consecutive_no_progress=2, replan=plan)) == "REPLAN"

def test_answer_must_be_correlated_before_pending_clears():
    assert gate.decide(state(pending_request={"marker":"x"}, new_completed_result=True)) == "WAIT"
    assert gate.decide(state(pending_request=None, new_completed_result=True)) == "REVIEW"

def test_invalid_plan_even_before_failure():
    assert gate.decide(state(replan={"hypothesis":"x"})) == "INVALID_STATE"

def test_resolved_blocker_reassessment():
    assessment = dict(for_cycle="cycle-3", evidence_ref="verified/access", reason="access restored")
    assert gate.decide(state(new_completed_result=False, reassessment=assessment)) == "REVIEW"
    assert gate.decide(state(new_completed_result=False, reassessment=assessment, paused=True)) == "PAUSED"

def test_old_reassessment_does_not_unlock():
    assessment = dict(for_cycle="old", evidence_ref="verified/access", reason="restored")
    assert gate.decide(state(new_completed_result=False, reassessment=assessment)) == "NO_NEW_RESULT"

def test_invalid_reassessment():
    assert gate.decide(state(reassessment={})) == "INVALID_STATE"
