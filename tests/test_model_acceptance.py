from birzha.application.model_lab import assess_walk_forward
from birzha.domain.validation import HorizonValidationMetrics, WalkForwardReport


def _report(hits: int, directional: int = 30, observations: int = 30) -> WalkForwardReport:
    metrics = tuple(
        HorizonValidationMetrics(
            sessions=sessions,
            observations=observations,
            directional_observations=directional,
            direction_hits=hits,
            direction_hit_rate=round(hits / directional, 6) if directional else None,
            mean_actual_return_pct=1.0,
            mean_absolute_error_pct=1.0,
            mean_signed_error_pct=0.0,
        )
        for sessions in (5, 10, 20)
    )
    return WalkForwardReport(
        symbol="SBER",
        start_date="2025-01-01",
        end_date="2026-01-01",
        requested_points=observations,
        completed_forecasts=observations,
        failed_forecasts=0,
        engine_versions=("TEST",),
        metrics=metrics,
        failures=(),
        status="COMPUTED",
    )


def test_small_sample_is_never_accepted() -> None:
    result = assess_walk_forward(_report(9, directional=10, observations=10))
    assert result.status == "INSUFFICIENT_SAMPLE"
    assert all(item.status == "INSUFFICIENT_SAMPLE" for item in result.horizons)


def test_weak_directional_model_is_rejected() -> None:
    result = assess_walk_forward(_report(17))
    assert result.status == "REJECTED"
    assert all(item.status == "REJECTED" for item in result.horizons)
    assert all(item.wilson_lower_95 is not None for item in result.horizons)


def test_strong_large_sample_can_be_accepted() -> None:
    result = assess_walk_forward(_report(26))
    assert result.status == "ACCEPTED"
    assert all(item.status == "ACCEPTED" for item in result.horizons)
    assert all(item.wilson_lower_95 > 0.5 for item in result.horizons if item.wilson_lower_95 is not None)


def test_low_directional_coverage_is_rejected() -> None:
    result = assess_walk_forward(_report(12, directional=15, observations=30))
    assert result.status == "REJECTED"
    assert all(any("directional_coverage" in reason for reason in item.reasons) for item in result.horizons)
