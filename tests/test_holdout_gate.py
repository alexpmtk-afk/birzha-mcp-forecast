from birzha.application.calibration import ModelCalibrationService, calibrate_across_symbols
from birzha.application.forecast import ForecastParameters
from birzha.domain.validation import HorizonAcceptance, ModelAcceptanceReport


def _accepted(symbol: str) -> ModelAcceptanceReport:
    horizons = tuple(
        HorizonAcceptance(
            sessions=s,
            observations=30,
            directional_observations=24,
            directional_coverage=0.8,
            direction_hit_rate=0.7,
            wilson_lower_95=0.55,
            minimum_observations=20,
            required_directional_coverage=0.7,
            required_wilson_lower=0.5,
            status="ACCEPTED",
            reasons=(),
        )
        for s in (5, 10, 20)
    )
    return ModelAcceptanceReport(
        symbol=symbol,
        start_date="2024-01-01",
        end_date="2026-01-01",
        engine_versions=("TEST",),
        walk_forward_status="COMPUTED",
        completed_forecasts=30,
        failed_forecasts=0,
        failures=(),
        horizons=horizons,
        status="ACCEPTED",
    )


def _rejected(symbol: str) -> ModelAcceptanceReport:
    item = _accepted(symbol)
    return ModelAcceptanceReport(
        symbol=item.symbol,
        start_date=item.start_date,
        end_date=item.end_date,
        engine_versions=item.engine_versions,
        walk_forward_status=item.walk_forward_status,
        completed_forecasts=item.completed_forecasts,
        failed_forecasts=item.failed_forecasts,
        failures=item.failures,
        horizons=item.horizons,
        status="REJECTED",
    )


def test_development_only_keeps_holdout_sealed(monkeypatch) -> None:
    events: list[str] = []

    class Assessor:
        def assess(self, symbol: str, *, start_date: str, **kwargs):
            assert start_date == "2024-01-01"
            events.append(f"development:{symbol}")
            return _accepted(symbol)

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: Assessor(),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]

    result = calibrate_across_symbols(
        service,
        ("SBER", "Si"),
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=(ForecastParameters(name="accepted"),),
        open_holdout=False,
        holdout_gate=lambda parameters: events.append("gate"),
    )

    assert result.status == "DEVELOPMENT_ACCEPTED_HOLDOUT_SEALED"
    assert result.holdout == ()
    assert events == ["development:SBER", "development:Si"]


def test_holdout_gate_runs_before_first_holdout_read(monkeypatch) -> None:
    events: list[str] = []

    class Assessor:
        def assess(self, symbol: str, *, start_date: str, **kwargs):
            if start_date == "2024-01-01":
                events.append(f"development:{symbol}")
                return _accepted(symbol)
            events.append(f"holdout:{symbol}")
            assert "gate" in events
            return _accepted(symbol)

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: Assessor(),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]

    result = calibrate_across_symbols(
        service,
        ("SBER", "Si"),
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=(ForecastParameters(name="accepted"),),
        open_holdout=True,
        holdout_gate=lambda parameters: events.append("gate"),
    )

    assert result.status == "ACCEPTED"
    assert events == [
        "development:SBER",
        "development:Si",
        "gate",
        "holdout:SBER",
        "holdout:Si",
    ]


def test_holdout_gate_is_not_called_when_development_fails(monkeypatch) -> None:
    gate_calls: list[str] = []

    class Assessor:
        def assess(self, symbol: str, *, start_date: str, **kwargs):
            assert start_date == "2024-01-01"
            return _rejected(symbol)

    monkeypatch.setattr(
        ModelCalibrationService,
        "_acceptance_for",
        lambda self, parameters: Assessor(),
    )
    service = ModelCalibrationService(validator=object())  # type: ignore[arg-type]

    result = calibrate_across_symbols(
        service,
        ("SBER", "Si"),
        development_start="2024-01-01",
        split_date="2025-01-01",
        holdout_end="2026-01-01",
        candidates=(ForecastParameters(name="rejected"),),
        open_holdout=True,
        holdout_gate=lambda parameters: gate_calls.append(parameters.name),
    )

    assert result.status == "REJECTED"
    assert result.holdout == ()
    assert gate_calls == []
