from birzha.application.model_lab import assess_walk_forward
from birzha.application.validation import (
    independent_sample_capacity,
    summarize_walk_forward,
)
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.domain.outcome import HorizonOutcome
from birzha.domain.validation import WalkForwardReport


def _forecast(index: int) -> ForecastRecord:
    return ForecastRecord(
        forecast_id=f"fcst_{index}",
        symbol="SBER",
        secid="SBER",
        created_at_t0=f"2025-01-{index + 1:02d}T18:50:00+03:00",
        engine_version="TEST",
        direction="UP",
        signal_strength=1.0,
        control="BUYERS",
        route="TREND",
        horizons=tuple(
            HorizonForecast(
                sessions=sessions,
                direction="UP",
                signal_strength=1.0,
                expected_move_pct=1.0,
                adverse_move_pct=1.0,
            )
            for sessions in (5, 10, 20)
        ),
        reasons=(),
        warnings=(),
        validation_status="TEST",
        reference_price=100.0,
    )


def _outcome(index: int, sessions: int, *, hit: bool) -> HorizonOutcome:
    return HorizonOutcome(
        outcome_id=f"out_{index}_{sessions}",
        forecast_id=f"fcst_{index}",
        symbol="SBER",
        secid="SBER",
        horizon_sessions=sessions,
        reference_price=100.0,
        target_session_end="2025-12-31T18:50:00+03:00",
        target_close=101.0,
        actual_return_pct=1.0,
        direction_hit=hit,
        max_favorable_excursion_pct=1.0,
        max_adverse_excursion_pct=-1.0,
    )


def _pairs(count: int):
    pairs = []
    for index in range(count):
        forecast = _forecast(index)
        for sessions in (5, 10, 20):
            pairs.append(
                (forecast, _outcome(index, sessions, hit=(index % 2 == 0)))
            )
    return pairs


def test_statistics_thin_overlapping_windows_by_horizon() -> None:
    metrics = {
        item.sessions: item
        for item in summarize_walk_forward(_pairs(8), step_sessions=5)
    }

    assert metrics[5].raw_observations == 8
    assert metrics[5].sampling_stride == 1
    assert metrics[5].observations == 8
    assert metrics[5].direction_hit_rate == 0.5

    assert metrics[10].raw_observations == 8
    assert metrics[10].sampling_stride == 2
    assert metrics[10].observations == 4
    assert metrics[10].direction_hit_rate == 1.0

    assert metrics[20].raw_observations == 8
    assert metrics[20].sampling_stride == 4
    assert metrics[20].observations == 2
    assert metrics[20].direction_hit_rate == 1.0


def test_stride_is_one_when_forecast_spacing_already_covers_horizon() -> None:
    metrics = {
        item.sessions: item
        for item in summarize_walk_forward(_pairs(8), step_sessions=10)
    }

    assert metrics[5].sampling_stride == 1
    assert metrics[10].sampling_stride == 1
    assert metrics[20].sampling_stride == 2


def test_many_overlapping_raw_forecasts_do_not_fake_minimum_sample() -> None:
    metric = next(
        item
        for item in summarize_walk_forward(_pairs(40), step_sessions=5)
        if item.sessions == 20
    )
    assert metric.raw_observations == 40
    assert metric.observations == 10

    report = WalkForwardReport(
        symbol="SBER",
        start_date="2025-01-01",
        end_date="2025-12-31",
        requested_points=40,
        completed_forecasts=40,
        failed_forecasts=0,
        engine_versions=("TEST",),
        metrics=(metric,),
        failures=(),
        status="COMPUTED",
        step_sessions=5,
    )
    assessed = assess_walk_forward(report, minimum_observations=20)

    assert assessed.status == "INSUFFICIENT_SAMPLE"
    assert assessed.horizons[0].observations == 10
    assert assessed.horizons[0].status == "INSUFFICIENT_SAMPLE"


def test_capacity_exposes_old_24_point_limit_as_insufficient_for_long_horizon() -> None:
    capacity = independent_sample_capacity(
        220, step_sessions=5, max_points=24
    )

    assert capacity == {5: 24, 10: 12, 20: 6}


def test_capacity_can_reach_twenty_long_horizon_samples_with_enough_history() -> None:
    capacity = independent_sample_capacity(
        500, step_sessions=5, max_points=80
    )

    assert capacity == {5: 80, 10: 40, 20: 20}


def test_non_overlapping_summary_rejects_invalid_step() -> None:
    try:
        summarize_walk_forward(_pairs(2), step_sessions=0)
    except ValueError as exc:
        assert "step_sessions must be > 0" in str(exc)
    else:
        raise AssertionError("invalid step_sessions was accepted")
