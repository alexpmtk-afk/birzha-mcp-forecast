"""The release blocker breakdown must be complete and never grant release."""
from copy import deepcopy
from pathlib import Path
import json
import pytest

from scripts.g2_release_blocker_triage import NEXT, main, triage
from scripts.g2_release_gate import REQUIRED, validate

MANIFEST = Path(__file__).resolve().parents[1] / "docs/G2_RELEASE_GATE_20261010.json"


def base():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_diagnostic_is_comprehensive_and_release_stays_blocked(capsys):
    found = triage(base())
    assert found["blocking_count"] == len(validate(base())["blocking_requirements"])
    assert found["blocking_technical_and_evidence"] == sum(v["status"] != "PASS" for v in base()["release_gates"].values())
    assert found["blocking_user_permissions"] == 2
    assert found["historical_receipt_missing_is_irrecoverable"] is True
    assert found["no_policy_change"] is True
    assert found["release_allowed"] is False
    assert len({item["gate"] for item in found["blocking"]}) == found["blocking_count"]
    assert set(NEXT) == REQUIRED | {"user_approved_main_merge", "user_approved_home_deploy"}
    main(["--manifest", str(MANIFEST)])
    out = capsys.readouterr().out
    assert "G2_RELEASE_ALLOWED=false" in out and "G2_BLOCKERS=" + str(found["blocking_count"]) in out


def test_legacy_missing_knowledge_cannot_be_relabelled_as_pass():
    found = triage(base())
    legacy = next(x for x in found["blocking"] if x["gate"] == "historical_first_receipt_attestation")
    assert legacy["category"] == "LEGACY_EVIDENCE_NOT_RECOVERABLE"
    assert legacy["status"] == "BLOCKED"
    assert "Do not backfill" in legacy["next_action"]


def test_incorrect_explicit_release_cannot_be_excused_by_diagnostic():
    m = deepcopy(base())
    m["release_authorized"] = True
    with pytest.raises(ValueError, match="release_authorized contradicts"):
        triage(m)


def test_diagnostic_reports_fewer_open_items_but_no_release_if_review_done():
    m = deepcopy(base())
    m["release_gates"]["individual_pr_semantic_review"]["status"] = "PASS"
    checked = triage(m)
    assert checked["blocking_count"] == len(triage(base())["blocking"]) - 1
    assert checked["release_allowed"] is False


def test_user_permissions_still_required_after_all_gates_pass():
    m = deepcopy(base())
    for gate in m["release_gates"].values():
        gate["status"] = "PASS"
    result = triage(m)
    assert result["blocking_count"] == 2
    assert result["blocking_user_permissions"] == 2
    assert not result["release_allowed"]


@pytest.mark.parametrize("missing", ["individual_pr_semantic_review", "user_approved_home_deploy"])
def test_triage_schema_missing_item_cannot_be_silent(monkeypatch, missing):
    copy = dict(NEXT)
    copy.pop(missing)
    monkeypatch.setattr("scripts.g2_release_blocker_triage.NEXT", copy)
    with pytest.raises(ValueError, match="not exhaustive"):
        triage(base())


def test_structured_output_contains_verified_blocker_evidence(capsys):
    main(["--manifest", str(MANIFEST), "--format", "json"])
    report = json.loads(capsys.readouterr().out)
    assert report["blocking_count"] == len(validate(base())["blocking_requirements"])
    assert all(b["evidence"] and b["next_action"] for b in report["blocking"])
    assert not report["release_allowed"]
