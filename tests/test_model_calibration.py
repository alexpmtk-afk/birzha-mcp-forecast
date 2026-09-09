from birzha.application.calibration import ModelCalibrationService
from birzha.application.forecast import ForecastParameters
from birzha.domain.validation import HorizonAcceptance, ModelAcceptanceReport


def _report(hit: float, wilson: float, status: str = "ACCEPTED") -> ModelAcceptanceReport:
    horizons = tuple(
        HorizonAcceptance(
            sessions=s,
            observations=30,
            directional_observations=24,
            directional_coverage=0.8,
            direction_hit_rate=hit,
            wilson_lower_95=wilson,
            minimum_observations=20,
            required_directional_coverage=0.7,
            required_wilson_lower=0.5,
            status=status,
            reasons=(),
        )
        for s in (5, 10, 20)
    )
    return ModelAcceptanceReport(
        symbol="SBER", start_date="2025-01-01", end_date="2025-12-31",
        engine_versions=("test",), walk_forward_status="COMPUTED",
        completed_forecasts=30, failed_forecasts=0, failures=(),
        horizons=horizons, status=status,
    )


def test_calibration_selects_on_development_then_checks_holdout(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    reports = {
        "baseline": _report(0.56, 0.46, "REJECTED"),
        "better": _report(0.70, 0.56, "ACCEPTED"),
    }

    class FakeAssessor:
        def __init__(self, name: str) -> None:
            self.name = name
        def assess(self, symbol: str, *, start_date: str, end_date: str, **kwargs):
            calls.append((self.name, start_date))
            return reports[self.name]

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: FakeAssessor(parameters.name),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]
    candidates = (
        ForecastParameters(name="baseline"),
        ForecastParameters(name="better", direction_threshold=0.8),
    )
    result = service.calibrate(
        "SBER",
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=candidates,
    )

    assert result.selected.name == "better"
    assert result.status == "ACCEPTED"
    assert calls.count(("baseline", "2024-01-01")) == 1
    assert calls.count(("better", "2024-01-01")) == 1
    assert calls.count(("better", "2025-01-01")) == 1
    assert ("baseline", "2025-01-01") not in calls


def test_multisymbol_calibration_uses_shared_parameters_and_all_holdouts(monkeypatch) -> None:
    from birzha.application.calibration import calibrate_across_symbols

    calls: list[tuple[str, str, str]] = []
    reports = {
        "baseline": {"SBER": _report(0.56, 0.46, "REJECTED"), "Si": _report(0.58, 0.47, "REJECTED")},
        "better": {"SBER": _report(0.70, 0.56, "ACCEPTED"), "Si": _report(0.68, 0.54, "ACCEPTED")},
    }

    class FakeAssessor:
        def __init__(self, name: str) -> None: self.name = name
        def assess(self, symbol: str, *, start_date: str, end_date: str, **kwargs):
            calls.append((self.name, symbol, start_date))
            return reports[self.name][symbol]

    monkeypatch.setattr(ModelCalibrationService, "_acceptance_for", lambda self, parameters: FakeAssessor(parameters.name))
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]
    candidates = (ForecastParameters(name="baseline"), ForecastParameters(name="better", direction_threshold=0.8))
    result = calibrate_across_symbols(
        service, ("SBER", "Si"),
        development_start="2024-01-01", split_date="2025-01-01", holdout_end="2026-01-01",
        candidates=candidates,
    )

    assert result.selected.name == "better"
    assert result.status == "ACCEPTED"
    assert ("baseline", "SBER", "2025-01-01") not in calls
    assert ("baseline", "Si", "2025-01-01") not in calls
    assert calls.count(("better", "SBER", "2025-01-01")) == 1
    assert calls.count(("better", "Si", "2025-01-01")) == 1
