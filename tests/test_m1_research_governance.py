"""M1 governance uses synthetic session indices only, not market/holdout data."""

from dataclasses import replace

from birzha.application.m1_research_governance import (
    OutcomeSample, SplitPlan, audit_m1_research_split,
)

FINGERPRINT = "a" * 64


def _plan():
    return SplitPlan(
        train_first=0, train_last=100,
        development_first=121, development_last=200,
        embargo_sessions=20, candidate_grid_hash="b" * 64,
        metric_id="FROZEN_SYNTHETIC_METRIC", tie_break_id="PREDECLARED_ORDER",
        calendar_version="SYNTHETIC_SESSIONS_V1", source_fingerprint=FINGERPRINT,
    )


def _samples():
    return [
        OutcomeSample("TRAIN", 50, 20, 70, 71, FINGERPRINT),
        OutcomeSample("DEVELOPMENT", 130, 5, 135, 136, FINGERPRINT),
    ]


def _audit(plan=None, records=None):
    return audit_m1_research_split(plan or _plan(), _samples() if records is None else records)


def _has_issue(report, phrase):
    return any(phrase in i for i in report["issues"])


def test_synthetic_split_preflight_never_approves_research_or_forecast():
    report = _audit()
    assert report["status"] == "RESEARCH_PREFLIGHT_ONLY"
    assert report["issues"] == []
    assert report["selection_authorized"] is False
    assert report["forecast_allowed"] is False
    assert report["final_holdout_accessed"] is False
    assert report["parameters_fitted"] is False


def test_reject_less_than_20_session_embargo():
    report = _audit(replace(_plan(), embargo_sessions=19))
    assert _has_issue(report, "maximal 20-session outcome")


def test_reject_insufficient_calendar_gap_even_when_embargo_is_20():
    report = _audit(replace(_plan(), development_first=120))
    assert _has_issue(report, "insufficient purged")


def test_reject_outcome_available_after_training_window():
    records = _samples()
    records[0] = replace(records[0], forecast_session=85, outcome_end_session=105, outcome_available_session=106)
    assert _has_issue(_audit(records=records), "outcome matures outside split")


def test_reject_outcome_available_after_development_window():
    records = _samples()
    records[1] = replace(records[1], forecast_session=198, outcome_end_session=203, outcome_available_session=203)
    assert _has_issue(_audit(records=records), "outcome matures outside split")


def test_reject_outcome_available_before_final_session():
    records = _samples()
    records[0] = replace(records[0], outcome_available_session=69)
    assert _has_issue(_audit(records=records), "before it ends")


def test_reject_horizon_off_by_one():
    records = _samples()
    records[0] = replace(records[0], outcome_end_session=69)
    assert _has_issue(_audit(records=records), "exact H")


def test_reject_unsupported_horizon():
    records = _samples()
    records[0] = replace(records[0], horizon_sessions=7)
    assert _has_issue(_audit(records=records), "unsupported horizon")


def test_holdout_samples_are_not_admitted_by_research_preflight():
    records = _samples()
    records.append(OutcomeSample("FINAL_HOLDOUT", 220, 5, 225, 226, FINGERPRINT))
    report = _audit(records=records)
    assert _has_issue(report, "FINAL_HOLDOUT must remain sealed")
    assert report["final_holdout_accessed"] is False


def test_reject_source_mismatch():
    records = _samples()
    records[0] = replace(records[0], source_fingerprint="c" * 64)
    assert _has_issue(_audit(records=records), "fingerprint mismatch")


def test_reject_duplicate_training_origins():
    records = _samples()
    records.append(records[0])
    assert _has_issue(_audit(records=records), "duplicate sample")


def test_reject_ambiguous_candidate_hash():
    assert _has_issue(_audit(replace(_plan(), candidate_grid_hash="TBD")), "candidate_grid_hash")


def test_no_data_is_not_a_pass():
    report = _audit(records=[])
    assert report["status"] == "CONFIG_REQUIRED"
    assert _has_issue(report, "no TRAIN/DEVELOPMENT receipts")


def test_both_splits_required():
    report = _audit(records=_samples()[:1])
    assert _has_issue(report, "both TRAIN and DEVELOPMENT")


def test_boolean_session_index_is_invalid():
    records = _samples()
    records[0] = replace(records[0], horizon_sessions=True)
    assert _has_issue(_audit(records=records), "invalid session ordinal")


def test_deterministic_result_for_same_inputs():
    assert _audit() == _audit()
