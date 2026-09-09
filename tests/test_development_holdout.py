from birzha.application.model_lab import ModelAcceptanceService
from birzha.domain.validation import HorizonValidationMetrics, WalkForwardReport


def _report(start: str, end: str, *, hits: int = 30, directional: int = 30) -> WalkForwardReport:
    metrics = tuple(
        HorizonValidationMetrics(
            sessions=sessions,
            observations=30,
            directional_observations=directional,
            direction_hits=hits,
            direction_hit_rate=hits / directional if directional else None,
            mean_actual_return_pct=1.0,
            mean_absolute_error_pct=1.0,
            mean_signed_error_pct=0.0,
        )
        for sessions in (5, 10, 20)
    )
    return WalkForwardReport(
        symbol="SBER", start_date=start, end_date=end, requested_points=30,
        completed_forecasts=30, failed_forecasts=0,
        engine_versions=("TEST",), metrics=metrics, failures=(), status="COMPUTED",
    )


class _Validator:
    def __init__(self, reports):
        self.reports = list(reports)
        self.calls = []

    def run(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return self.reports.pop(0)


def test_holdout_requires_both_periods_to_pass() -> None:
    validator = _Validator([
        _report("2024-01-01", "2025-01-01"),
        _report("2025-01-01", "2026-01-01"),
    ])
    result = ModelAcceptanceService(validator).assess_development_holdout(
        "SBER", development_start="2024-01-01", split_date="2025-01-01", holdout_end="2026-01-01"
    )
    assert result.status == "ACCEPTED"
    assert result.development.status == "ACCEPTED"
    assert result.holdout.status == "ACCEPTED"
    assert len(validator.calls) == 2


def test_holdout_rejects_model_that_fails_later_period() -> None:
    validator = _Validator([
        _report("2024-01-01", "2025-01-01"),
        _report("2025-01-01", "2026-01-01", hits=16, directional=30),
    ])
    result = ModelAcceptanceService(validator).assess_development_holdout(
        "SBER", development_start="2024-01-01", split_date="2025-01-01", holdout_end="2026-01-01"
    )
    assert result.status == "REJECTED"
    assert result.development.status == "ACCEPTED"
    assert result.holdout.status == "REJECTED"
