"""Tests for H1 duration audit without any user datasets or network access."""
from copy import deepcopy

import pytest

from scripts.g2_h1_duration_forensics import audit_bundle, INPUT_SCHEMA


def _bar(begin, end, completed):
    return {"begin": begin, "end": end, "completed": completed}


def _case():
    return {
        "case_id": "IMOEX-2025-03-31",
        "instrument": {"symbol": "IMOEX", "secid": "IMOEX"},
        "series": {"H1": [
            _bar("2025-03-31 17:00:00", "2025-03-31 17:59:59", True),
            _bar("2025-03-31 18:00:00", "2025-03-31 18:54:02", False),
        ]},
    }


def _inputs():
    return {"schema": INPUT_SCHEMA,
            "scope": "RECONSTRUCTED_HISTORICAL_PRICE_ENGINE_ONLY",
            "cases": [_case()]}


def _receipt():
    return {"duration_normalization": [
        {"case_id": "IMOEX-2025-03-31", "timeframe": "H1",
         "incomplete_duration_marked": 1},
    ]}


def test_one_duration_marked_close_does_not_certify_incomplete_market_session():
    inputs = _inputs()
    original = deepcopy(inputs)
    got = audit_bundle(inputs, _receipt())
    assert inputs == original
    assert got["total_counts"]["flagged_false"] == 1
    assert got["total_counts"]["false_from_duration_rule"] == 1
    assert got["window_status"]["INSUFFICIENT_50_ROWS"] == 1
    assert got["total_counts"]["no_next_row_in_context"] == 1
    assert got["original_source_completed_bit_recoverable"] is False
    assert got["all_flagged_bars_known_incomplete"] is False
    assert got["independent_first_receipt_attested"] is False
    assert got["admitted_to_forecast"] is False


def test_next_bar_same_date_only_proves_later_observation_not_completion():
    b = _inputs()
    b["cases"][0]["series"]["H1"].append(
        _bar("2025-03-31 19:00:00", "2025-03-31 19:59:59", True)
    )
    got = audit_bundle(b, _receipt())
    assert got["total_counts"]["later_h1_same_date"] == 1
    assert got["all_flagged_bars_known_incomplete"] is False


def test_duplicated_case_rejected():
    b = _inputs()
    b["cases"].append(deepcopy(b["cases"][0]))
    with pytest.raises(ValueError, match="duplicate case"):
        audit_bundle(b, _receipt())


def test_receipt_count_must_match_duration_flag_count():
    r = _receipt()
    r["duration_normalization"][0]["incomplete_duration_marked"] = 0
    with pytest.raises(ValueError, match="receipt does not support"):
        audit_bundle(_inputs(), r)


def test_short_true_or_long_false_conflict_is_not_silently_accepted():
    b = _inputs()
    b["cases"][0]["series"]["H1"][1]["completed"] = True
    with pytest.raises(ValueError, match="completed H1 conflicts"):
        audit_bundle(b, _receipt())

    b = _inputs()
    b["cases"][0]["series"]["H1"][1]["end"] = "2025-03-31 18:59:59"
    with pytest.raises(ValueError, match="false H1 not explained"):
        audit_bundle(b, _receipt())


def test_unsorted_and_nonpositive_duration_rejected():
    b = _inputs()
    b["cases"][0]["series"]["H1"][1]["begin"] = "2025-03-31 16:00:00"
    with pytest.raises(ValueError, match="invalid/unsorted"):
        audit_bundle(b, _receipt())
    b = _inputs()
    b["cases"][0]["series"]["H1"][1]["end"] = "2025-03-31 17:59:59"
    with pytest.raises(ValueError, match="invalid/unsorted"):
        audit_bundle(b, _receipt())


def test_empty_h1_cannot_be_reclassified_as_complete_window():
    b = _inputs()
    b["cases"][0]["series"]["H1"] = []
    r = _receipt()
    r["duration_normalization"][0]["incomplete_duration_marked"] = 0
    got = audit_bundle(b, r)
    assert got["window_status"] == {"INSUFFICIENT_50_ROWS": 1}
    assert got["total_counts"]["total_rows"] == 0


@pytest.mark.parametrize("value", [None, 0, "yes"])
def test_nonboolean_completed_field_rejected(value):
    b = _inputs()
    b["cases"][0]["series"]["H1"][1]["completed"] = value
    with pytest.raises(ValueError, match="non-boolean completed"):
        audit_bundle(b, _receipt())


def test_duplicate_receipt_key_rejected():
    r = _receipt()
    r["duration_normalization"].append(deepcopy(r["duration_normalization"][0]))
    with pytest.raises(ValueError, match="duplicate receipt"):
        audit_bundle(_inputs(), r)


def test_wrong_scope_or_schema_rejected():
    b = _inputs()
    b["scope"] = "PRODUCTION"
    with pytest.raises(ValueError, match="wrong research bundle"):
        audit_bundle(b, _receipt())
