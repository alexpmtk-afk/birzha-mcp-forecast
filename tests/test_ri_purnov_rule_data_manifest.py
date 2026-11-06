"""Regression checks for the RI Purnov research source-data contract only.

No market request, Google mutation, signal generation or trading.
"""
import json
from collections import Counter
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "ri_purnov_rule_data_manifest_v1.json"
STATUSES = {
    "FIELD_PRESENT": 16,
    "PARTIAL_FIELDS": 29,
    "PROXY_ONLY": 14,
    "MISSING_OR_UNIMPLEMENTED": 18,
    "PROCEDURE_NOT_COLUMN": 10,
}
SHEETS = {
    "D1", "H1", "M15", "D1_INDICATORS", "H1_INDICATORS",
    "M15_INDICATORS", "SESSIONS", "CONTRACTS",
    "QUALITY_AUDIT", "M1_SOURCE_INDEX", "STORAGE_RULES",
    "VERIFIED_RANGES", "SYNC_STATUS",
}


def load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_all_87_purnov_rules_have_unique_evidence_records():
    manifest = load()
    rules = manifest["rules"]
    assert len(rules) == 87
    assert [r["rule_id"] for r in rules] == [f"P3-{i:03d}" for i in range(1, 88)]
    assert Counter(r["current_evidence_status"] for r in rules) == STATUSES
    assert all(r["independent_verified_cases"] == 0 for r in rules)


def test_field_references_are_existing_sheet_scopes_not_new_workbooks():
    rules = load()["rules"]
    for rule in rules:
        assert rule["section"] and rule["condition"]
        assert isinstance(rule["existing_field_refs"], list)
        assert len(rule["existing_field_refs"]) == len(set(rule["existing_field_refs"]))
        for ref in rule["existing_field_refs"]:
            sheet, field = ref.split(":")
            assert sheet in SHEETS and field and " " not in field


def test_contract_does_not_claim_production_or_true_trade_evidence():
    data = load()
    assert data["status"] == "RESEARCH_ONLY"
    assert data["forecast_admission"] == "BLOCKED"
    assert data["field_presence_is_not_rule_implementation"] is True
    assert data["source_time_available_at"] == "UNKNOWN"
    assert data["exact_price_level_volume"] == "UNAVAILABLE"
    assert data["exact_aggressor_delta"] == "UNAVAILABLE"
    assert data["canonical_spreadsheet_id"] == "1PuPv7Nv_Dpj-KTECPHNHecj34HrR9K5y53OwRcu7RVY"
