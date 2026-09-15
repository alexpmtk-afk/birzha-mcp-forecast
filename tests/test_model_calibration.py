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
    assert result.holdout is not None
    assert calls.count(("baseline", "2024-01-01")) == 1
    assert calls.count(("better", "2024-01-01")) == 1
    assert calls.count(("better", "2025-01-02")) == 1
    assert ("baseline", "2025-01-02") not in calls
    assert ("better", "2025-01-01") not in calls


def test_single_symbol_calibration_prefers_accepted_over_higher_rejected_score(monkeypatch) -> None:
    reports = {
        "flashy_rejected": _report(0.95, 0.80, "REJECTED"),
        "robust_accepted": _report(0.66, 0.52, "ACCEPTED"),
    }

    class FakeAssessor:
        def __init__(self, name: str) -> None:
            self.name = name

        def assess(self, symbol: str, **kwargs):
            return reports[self.name]

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: FakeAssessor(parameters.name),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]
    result = service.calibrate(
        "SBER",
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=(
            ForecastParameters(name="flashy_rejected"),
            ForecastParameters(name="robust_accepted", direction_threshold=0.8),
        ),
    )

    assert result.selected.name == "robust_accepted"


def test_single_symbol_rejected_development_never_opens_holdout(monkeypatch) -> None:
    calls: list[str] = []

    class FakeAssessor:
        def assess(self, symbol: str, *, start_date: str, **kwargs):
            calls.append(start_date)
            if start_date != "2024-01-01":
                raise AssertionError("holdout must remain untouched after development rejection")
            return _report(0.95, 0.80, "REJECTED")

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: FakeAssessor(),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]
    result = service.calibrate(
        "SBER",
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=(ForecastParameters(name="rejected"),),
    )

    assert result.status == "REJECTED"
    assert result.holdout is None
    assert calls == ["2024-01-01"]
    assert result.to_dict()["holdout_evaluated"] is False


def test_multisymbol_calibration_uses_shared_parameters_and_all_holdouts(monkeypatch) -> None:
    from birzha.application.calibration import calibrate_across_symbols

    calls: list[tuple[str, str, str]] = []
    reports = {
        "baseline": {"SBER": _report(0.56, 0.46, "REJECTED"), "Si": _report(0.58, 0.47, "REJECTED")},
        "better": {"SBER": _report(0.70, 0.56, "ACCEPTED"), "Si": _report(0.68, 0.54, "ACCEPTED")},
    }

    class FakeAssessor:
        def __init__(self, name: str) -> None:
            self.name = name

        def assess(self, symbol: str, *, start_date: str, end_date: str, **kwargs):
            calls.append((self.name, symbol, start_date))
            return reports[self.name][symbol]

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
    result = calibrate_across_symbols(
        service,
        ("SBER", "Si"),
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=candidates,
    )

    assert result.selected.name == "better"
    assert result.status == "ACCEPTED"
    assert ("baseline", "SBER", "2025-01-02") not in calls
    assert ("baseline", "Si", "2025-01-02") not in calls
    assert calls.count(("better", "SBER", "2025-01-02")) == 1
    assert calls.count(("better", "Si", "2025-01-02")) == 1
    assert ("better", "SBER", "2025-01-01") not in calls
    assert ("better", "Si", "2025-01-01") not in calls


def test_multisymbol_calibration_prefers_candidate_accepted_on_every_market(monkeypatch) -> None:
    from birzha.application.calibration import calibrate_across_symbols

    reports = {
        "average_star": {
            "SBER": _report(0.95, 0.80, "ACCEPTED"),
            "Si": _report(0.95, 0.80, "REJECTED"),
        },
        "robust_all": {
            "SBER": _report(0.66, 0.52, "ACCEPTED"),
            "Si": _report(0.66, 0.52, "ACCEPTED"),
        },
    }

    class FakeAssessor:
        def __init__(self, name: str) -> None:
            self.name = name

        def assess(self, symbol: str, **kwargs):
            return reports[self.name][symbol]

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: FakeAssessor(parameters.name),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]
    result = calibrate_across_symbols(
        service,
        ("SBER", "Si"),
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=(
            ForecastParameters(name="average_star"),
            ForecastParameters(name="robust_all", direction_threshold=0.8),
        ),
    )

    assert result.selected.name == "robust_all"


def test_multisymbol_rejected_development_never_opens_holdout(monkeypatch) -> None:
    from birzha.application.calibration import calibrate_across_symbols

    calls: list[tuple[str, str]] = []

    class FakeAssessor:
        def assess(self, symbol: str, *, start_date: str, **kwargs):
            calls.append((symbol, start_date))
            if start_date != "2024-01-01":
                raise AssertionError("holdout must remain untouched after development rejection")
            return _report(0.70, 0.55, "REJECTED")

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: FakeAssessor(),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]
    result = calibrate_across_symbols(
        service,
        ("SBER", "Si"),
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=(ForecastParameters(name="rejected"),),
    )

    assert result.status == "REJECTED"
    assert result.holdout == ()
    assert calls == [("SBER", "2024-01-01"), ("Si", "2024-01-01")]
    assert result.to_dict()["holdout_evaluated"] is False


def test_multisymbol_fully_evaluated_candidate_beats_candidate_with_failed_market(monkeypatch) -> None:
    from birzha.application.calibration import calibrate_across_symbols

    symbols = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
    incomplete_status = {
        "SBER": "ACCEPTED",
        "Si": "ACCEPTED",
        "BR": "ACCEPTED",
        "GOLD": "ACCEPTED",
        "IMOEX": "ACCEPTED",
        "RTSI": "FAILED",
    }
    complete_status = {
        "SBER": "ACCEPTED",
        "Si": "ACCEPTED",
        "BR": "ACCEPTED",
        "GOLD": "ACCEPTED",
        "IMOEX": "REJECTED",
        "RTSI": "REJECTED",
    }

    class FakeAssessor:
        def __init__(self, name: str) -> None:
            self.name = name

        def assess(self, symbol: str, *, start_date: str, **kwargs):
            if start_date != "2024-01-01":
                raise AssertionError("neither candidate passed all development markets")
            status = (
                incomplete_status[symbol]
                if self.name == "five_plus_failure"
                else complete_status[symbol]
            )
            return _report(0.90, 0.75, status)

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: FakeAssessor(parameters.name),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]
    result = calibrate_across_symbols(
        service,
        symbols,
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=(
            ForecastParameters(name="five_plus_failure"),
            ForecastParameters(name="four_complete", direction_threshold=0.8),
        ),
    )

    assert result.selected.name == "four_complete"
    assert result.status == "REJECTED"
    assert result.holdout == ()
