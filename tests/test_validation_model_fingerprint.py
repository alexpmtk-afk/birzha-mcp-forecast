from birzha.application.forecast import ForecastParameters
import scripts.run_authorized_ydb_validation as validation


def test_holdout_fingerprint_changes_with_engine_version(monkeypatch) -> None:
    parameters = ForecastParameters(name="baseline")
    first = validation._model_fingerprint(
        parameters,
        development_start="2021-01-01",
        split_date="2022-12-31",
    )

    monkeypatch.setattr(validation, "ENGINE_VERSION", "TEST_DIFFERENT_ENGINE")
    second = validation._model_fingerprint(
        parameters,
        development_start="2021-01-01",
        split_date="2022-12-31",
    )

    assert first != second


def test_holdout_fingerprint_changes_with_selected_parameters() -> None:
    baseline = validation._model_fingerprint(
        ForecastParameters(name="baseline"),
        development_start="2021-01-01",
        split_date="2022-12-31",
    )
    alternative = validation._model_fingerprint(
        ForecastParameters(name="alternative", direction_threshold=0.8),
        development_start="2021-01-01",
        split_date="2022-12-31",
    )

    assert baseline != alternative
