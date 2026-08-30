from birzha.application.validation import summarize_walk_forward
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.domain.outcome import HorizonOutcome


def _forecast(fid: str, direction: str, expected: float) -> ForecastRecord:
    return ForecastRecord(
        forecast_id=fid, symbol="SBER", secid="SBER", created_at_t0="2026-01-10T18:40:00+03:00",
        engine_version="engine", direction=direction, signal_strength=0.5,
        control="BUYERS" if direction == "UP" else "SELLERS", route="TREND",
        horizons=(HorizonForecast(5, direction, 0.5, expected, 1.0),),
        reasons=(), warnings=(), validation_status="UNVALIDATED_BASELINE", reference_price=100.0,
    )


def _outcome(fid: str, actual: float, hit: bool) -> HorizonOutcome:
    return HorizonOutcome(
        outcome_id=f"out_{fid}", forecast_id=fid, symbol="SBER", secid="SBER",
        horizon_sessions=5, reference_price=100.0, target_session_end="2026-01-17T18:40:00+03:00",
        target_close=100.0 + actual, actual_return_pct=actual, direction_hit=hit,
        max_favorable_excursion_pct=3.0, max_adverse_excursion_pct=-1.0,
    )


def test_summary_computes_direction_and_forecast_error_metrics():
    first = _forecast("a", "UP", 2.0)
    second = _forecast("b", "DOWN", -1.0)
    metrics = summarize_walk_forward([
        (first, _outcome("a", 3.0, True)),
        (second, _outcome("b", 2.0, False)),
    ])[0]

    assert metrics.sessions == 5
    assert metrics.observations == 2
    assert metrics.direction_hits == 1
    assert metrics.direction_hit_rate == 0.5
    assert metrics.mean_actual_return_pct == 2.5
    assert metrics.mean_absolute_error_pct == 2.0
    assert metrics.mean_signed_error_pct == 2.0
