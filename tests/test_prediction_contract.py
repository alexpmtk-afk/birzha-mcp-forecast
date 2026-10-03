from birzha.application.prediction import (
    BASELINE_BARRIER_K,
    DEFAULT_HORIZONS,
    build_prediction_contract,
)
from birzha.domain.snapshot import MarketSnapshot, TimeframeState


def _state(timeframe: str, close: float, atr_pct: float | None) -> TimeframeState:
    return TimeframeState(
        timeframe=timeframe,
        candles=100,
        last_close=close,
        return_5=None,
        return_10=None,
        return_20=None,
        sma_20=None,
        sma_50=None,
        efficiency_ratio_20=None,
        atr_14_pct=atr_pct,
        volume_ratio_20=None,
        trend_score=0.0,
    )


def _snapshot(symbol: str = "SBER", secid: str = "SBER") -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        secid=secid,
        as_of="2026-10-03T18:45:00+03:00",
        source="TEST",
        d1=_state("D1", 100.0, 0.02),
        h1=_state("H1", 101.0, 0.01),
        m15=_state("M15", 102.0, 0.005),
        data_quality="PASS",
        warnings=(),
    )


def test_prediction_contract_uses_causal_p0_and_d1_atr_barriers() -> None:
    contract = build_prediction_contract(_snapshot())

    assert contract.p0 == 102.0
    assert contract.price_coordinate == "LAST_COMPLETED_M15_CLOSE"
    assert contract.causal_volatility_price == 2.0
    assert contract.barrier_k == 1.0
    assert contract.up_barrier == 104.0
    assert contract.down_barrier == 100.0
    assert contract.horizons_sessions == (5, 10, 20)


def test_prediction_contract_uses_one_universal_baseline_k() -> None:
    sber = build_prediction_contract(_snapshot("SBER", "SBER"))
    si = build_prediction_contract(_snapshot("Si", "SiZ6"))

    assert BASELINE_BARRIER_K == 1.0
    assert sber.barrier_k == si.barrier_k == 1.0
    assert sber.up_barrier - sber.p0 == si.up_barrier - si.p0


def test_prediction_contract_is_deterministic() -> None:
    first = build_prediction_contract(_snapshot())
    second = build_prediction_contract(_snapshot())

    assert first.contract_id == second.contract_id
    assert first.to_dict() == second.to_dict()


def test_prediction_contract_fails_without_causal_atr() -> None:
    snapshot = _snapshot()
    snapshot = MarketSnapshot(
        symbol=snapshot.symbol,
        secid=snapshot.secid,
        as_of=snapshot.as_of,
        source=snapshot.source,
        d1=_state("D1", 100.0, None),
        h1=snapshot.h1,
        m15=snapshot.m15,
        data_quality=snapshot.data_quality,
        warnings=snapshot.warnings,
    )

    try:
        build_prediction_contract(snapshot)
    except ValueError as exc:
        assert "ATR" in str(exc)
    else:
        raise AssertionError("missing ATR must fail closed")


def test_prediction_contract_horizons_are_protocol_project_horizons() -> None:
    assert DEFAULT_HORIZONS == (5, 10, 20)
