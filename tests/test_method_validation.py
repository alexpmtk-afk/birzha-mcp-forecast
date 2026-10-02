from datetime import date
from types import SimpleNamespace

from birzha.application.method_signals import ForecastMethodSignal
from birzha.application.method_validation import (
    MethodWalkForwardValidator,
    _method_status,
    _summarize_direction_benchmarks,
    build_method_forecast_record,
)
from birzha.domain.outcome import HorizonOutcome
from birzha.domain.snapshot import MarketSnapshot, TimeframeState


def _state(tf: str) -> TimeframeState:
    return TimeframeState(
        timeframe=tf,
        candles=100,
        last_close=100.0,
        return_5=0.01,
        return_10=0.02,
        return_20=0.03,
        sma_20=99.0,
        sma_50=98.0,
        efficiency_ratio_20=0.5,
        atr_14_pct=0.01 if tf == "D1" else None,
        volume_ratio_20=1.1,
        trend_score=2.0,
        vwap_20=99.5,
        price_location_20=0.7,
        support_20=95.0,
        resistance_20=105.0,
    )


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="SBER",
        secid="SBER",
        as_of="2026-10-01T18:45:00+03:00",
        source="HISTORICAL_STORE",
        d1=_state("D1"),
        h1=_state("H1"),
        m15=_state("M15"),
        data_quality="PASS",
        warnings=(),
    )


def test_build_method_forecast_record_is_method_specific_and_deterministic() -> None:
    signal = ForecastMethodSignal(
        name="TREND_MOMENTUM",
        role="DIRECTIONAL",
        available=True,
        score=0.6,
        direction="UP",
        strength=0.6,
        evidence=("trend_up",),
    )

    first = build_method_forecast_record(_snapshot(), signal)
    second = build_method_forecast_record(_snapshot(), signal)

    assert first.forecast_id == second.forecast_id
    assert first.forecast_id.startswith("mfcst_")
    assert first.engine_version.endswith(":TREND_MOMENTUM")
    assert first.direction == "UP"
    assert [item.sessions for item in first.horizons] == [5, 10, 20]
    assert all(item.expected_move_pct is None for item in first.horizons)


def test_method_status_distinguishes_unavailable_from_failure() -> None:
    assert _method_status(requested=10, completed=10, unavailable=0, failures=0) == "COMPUTED"
    assert _method_status(requested=10, completed=5, unavailable=3, failures=2) == "PARTIAL"
    assert _method_status(requested=10, completed=0, unavailable=10, failures=0) == "UNAVAILABLE"
    assert _method_status(requested=10, completed=0, unavailable=0, failures=10) == "FAILED"


def test_method_validation_services_are_frozen_store_only() -> None:
    base = SimpleNamespace(
        history=object(),
        market_data=object(),
        historical_flow=SimpleNamespace(analytics=object(), store=object()),
        forecasts=SimpleNamespace(
            snapshots=SimpleNamespace(flow=object())
        ),
    )
    validator = MethodWalkForwardValidator(base=base)  # type: ignore[arg-type]

    snapshot_service, stored = validator._services(
        "Si",
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
    )

    assert stored.require_stored_resolution is True
    assert snapshot_service.flow is not None
    assert snapshot_service.flow.historical is not None
    assert snapshot_service.flow.historical.read_only is True


def test_method_validation_rejects_live_only_mode() -> None:
    base = SimpleNamespace(
        history=None,
        market_data=object(),
        historical_flow=None,
        forecasts=SimpleNamespace(snapshots=SimpleNamespace(flow=None)),
    )
    validator = MethodWalkForwardValidator(base=base)  # type: ignore[arg-type]

    try:
        validator._services(
            "SBER",
            start=date(2025, 1, 1),
            end=date(2025, 12, 31),
        )
    except RuntimeError as exc:
        assert "frozen stored history" in str(exc)
    else:
        raise AssertionError("live-only method validation must fail closed")



def _outcome(t0: str, value: float) -> tuple[str, HorizonOutcome]:
    return (
        t0,
        HorizonOutcome(
            outcome_id=f"out-{t0}-{value}",
            forecast_id=f"fcst-{t0}",
            symbol="SBER",
            secid="SBER",
            horizon_sessions=5,
            reference_price=100.0,
            target_session_end="2026-01-31T18:45:00+03:00",
            target_close=100.0 + value,
            actual_return_pct=value,
            direction_hit=None,
            max_favorable_excursion_pct=None,
            max_adverse_excursion_pct=None,
        ),
    )


def test_direction_benchmark_uses_real_market_base_rate() -> None:
    items = [
        _outcome("2026-01-01T18:45:00+03:00", 1.0),
        _outcome("2026-01-02T18:45:00+03:00", 2.0),
        _outcome("2026-01-03T18:45:00+03:00", -1.0),
        _outcome("2026-01-04T18:45:00+03:00", 0.0),
    ]

    report = _summarize_direction_benchmarks(items, step_sessions=5)

    assert len(report) == 1
    item = report[0]
    assert item.observations == 4
    assert item.up_moves == 2
    assert item.down_moves == 1
    assert item.flat_moves == 1
    assert item.always_up_hit_rate == 0.5
    assert item.always_down_hit_rate == 0.25
    assert item.majority_hit_rate == 0.5
