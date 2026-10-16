"""No data, pricing or external connection permitted for pre-registered plan."""
import hashlib
import json

import pytest

from scripts.g2_structure_pilot_plan import (
    ROOTS, DAYS, VERSION, frozen_plan, encoded_plan, write_exclusive,
)


def test_manifest_is_predeclared_and_does_not_claim_production():
    plan = frozen_plan()
    assert plan["version"] == VERSION
    assert len(DAYS) == 12
    assert plan["n_slots"] == 72
    assert len(plan["slots"]) == 72
    assert plan["horizons_sessions"] == [5, 10, 20]
    assert plan["real_capture_enabled"] is False
    assert plan["released_forecast_model"] is False
    assert plan["not_started"] is True
    assert plan["first_receipt_required_for_prediction"] is True
    assert plan["independent_timestamp_authority_provided"] is False
    assert plan["candidate"]["up"] == "D1=HH_HL and H1=HH_HL"
    assert plan["candidate"]["down"] == "D1=LH_LL and H1=LH_LL"


def test_each_slot_has_exact_moscow_t0_and_contract_unresolved_for_futures():
    plan = frozen_plan()
    assert len(set((r["root"],r["t0_moscow"]) for r in plan["slots"])) == 72
    assert all(r["t0_moscow"].endswith("T13:10:00+03:00") for r in plan["slots"])
    assert all(r["exact_secid"] is None for r in plan["slots"] if r["root"] in {"Si","BR","GOLD"})
    assert all(r["status"] == "PLANNED_NOT_CAPTURED" for r in plan["slots"])


def test_exclusive_file_is_deterministic_and_cannot_be_replaced(tmp_path):
    out = tmp_path / "preflight.json"
    expected = hashlib.sha256(encoded_plan()).hexdigest()
    assert write_exclusive(out) == expected
    assert json.loads(out.read_text()) == frozen_plan()
    with pytest.raises(FileExistsError):
        write_exclusive(out)
    assert hashlib.sha256(out.read_bytes()).hexdigest() == expected
