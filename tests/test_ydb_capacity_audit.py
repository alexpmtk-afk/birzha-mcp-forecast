from __future__ import annotations

import inspect

import scripts.run_authorized_ydb_capacity_audit as audit


def test_capacity_audit_never_imports_model_evaluation_or_holdout_governance() -> None:
    source = inspect.getsource(audit)
    forbidden = (
        "ModelCalibrationService",
        "WalkForwardValidator",
        "build_validation_dataset_fingerprint",
        "YdbValidationGovernanceStore",
        "MarketSnapshotService",
    )
    assert all(name not in source for name in forbidden)


def test_capacity_shortfall_keeps_model_unseen_and_holdout_sealed() -> None:
    result = audit._finalize_capacity_artifact(
        {},
        development_shortfall={"BR": {"20": 7}},
        holdout_shortfall={"BR": {"20": 8}},
    )

    assert result["run_status"] == "INSUFFICIENT_DATA"
    assert result["model_status"] == "NOT_EVALUATED"
    assert result["holdout_evaluated"] is False
    assert result["holdout_sealed"] is True
    assert result["capacity_shortfall"] == {
        "development": {"BR": {"20": 7}},
        "holdout": {"BR": {"20": 8}},
    }


def test_sufficient_capacity_still_does_not_evaluate_model_or_holdout() -> None:
    result = audit._finalize_capacity_artifact(
        {}, development_shortfall={}, holdout_shortfall={}
    )

    assert result["run_status"] == "CAPACITY_SUFFICIENT"
    assert result["model_status"] == "NOT_EVALUATED"
    assert result["holdout_evaluated"] is False
    assert result["holdout_sealed"] is True
    assert result["capacity_shortfall"] == {}


def test_capacity_threshold_remains_twenty() -> None:
    assert audit.MINIMUM_ACCEPTANCE_OBSERVATIONS == 20
