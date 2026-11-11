"""Read-only gate; evidence truth and permissions require independent review."""
import json
import sys

def decide(state):
    fields = ("enabled", "paused", "new_completed_result", "all_requirements_verified", "blocker_requires_owner", "initial_request")
    if not isinstance(state, dict) or any(type(state.get(k)) is not bool for k in fields):
        return "INVALID_STATE"
    n = state.get("consecutive_no_progress")
    if type(n) is not int or n < 0:
        return "INVALID_STATE"
    if "pending_request" not in state or (state["pending_request"] is not None and not isinstance(state["pending_request"], dict)):
        return "INVALID_STATE"
    refs = state.get("evidence_refs")
    if not isinstance(refs, list) or any(not isinstance(x, str) or not x.strip() for x in refs):
        return "INVALID_STATE"
    cycle = state.get("cycle_id")
    if not isinstance(cycle, str) or not cycle.strip():
        return "INVALID_STATE"
    plan = state.get("replan")
    keys = ("changed_factor", "hypothesis", "bounded_test", "expected_evidence", "for_cycle")
    if plan is not None and (not isinstance(plan, dict) or not all(isinstance(plan.get(k), str) and plan[k].strip() for k in keys)):
        return "INVALID_STATE"
    reassessment = state.get("reassessment")
    rekeys = ("for_cycle", "evidence_ref", "reason")
    if reassessment is not None and (not isinstance(reassessment, dict) or not all(isinstance(reassessment.get(k), str) and reassessment[k].strip() for k in rekeys)):
        return "INVALID_STATE"
    reassessed = reassessment is not None and reassessment["for_cycle"] == cycle
    if not state["enabled"] or state["paused"]:
        return "PAUSED"
    if state["pending_request"] is not None:
        return "WAIT"
    if state["initial_request"]:
        if n != 0 or state["new_completed_result"] or state["all_requirements_verified"]:
            return "INVALID_STATE"
        return "BLOCKED" if state["blocker_requires_owner"] else "START_REVIEW"
    if not state["new_completed_result"] and not reassessed:
        return "NO_NEW_RESULT"
    if state["blocker_requires_owner"]:
        return "BLOCKED"
    if state["all_requirements_verified"]:
        return "COMPLETE" if refs else "INVALID_STATE"
    if n >= 2 and (plan is None or plan["for_cycle"] != cycle):
        return "REPLAN"
    return "REVIEW"

if __name__ == "__main__":
    try:
        with open(sys.argv[1], encoding="utf-8-sig") as f:
            result = decide(json.load(f))
    except (OSError, ValueError, IndexError):
        result = "INVALID_STATE"
    print(json.dumps({"decision": result}))
    sys.exit(2 if result == "INVALID_STATE" else 0)
