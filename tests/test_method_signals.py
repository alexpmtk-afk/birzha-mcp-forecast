from birzha.application.method_signals import build_method_signals
from birzha.domain.flow import ClientOpenInterest, MarketFlowSnapshot
from birzha.domain.snapshot import MarketSnapshot, TimeframeState


def _state(
    tf: str,
    *,
    trend: float,
    r5: float,
    r20: float,
    r10: float | None = None,
    atr: float | None = None,
    er: float = 0.50,
    close: float = 102.0,
    vwap: float = 100.0,
    location: float = 0.80,
    volume_ratio: float = 1.10,
) -> TimeframeState:
    return TimeframeState(
        timeframe=tf,
        candles=100,
        last_close=close,
        return_5=r5,
        return_10=r5 * 1.5 if r10 is None else r10,
        return_20=r20,
        sma_20=99.0,
        sma_50=97.0,
        efficiency_ratio_20=er,
        atr_14_pct=atr,
        volume_ratio_20=volume_ratio,
        trend_score=trend,
        vwap_20=vwap,
        price_location_20=location,
        support_20=95.0,
        resistance_20=105.0,
    )


def _flow() -> MarketFlowSnapshot:
    return MarketFlowSnapshot(
        symbol="Si",
        secid="SiZ6",
        from_date="2026-10-01",
        till_date="2026-10-01",
        as_of="2026-10-01T18:45:00+03:00",
        source="MOEX_ISS_PUBLIC_TRADES_DERIVED+FUTOI",
        intervals=3,
        buy_volume=70.0,
        sell_volume=30.0,
        volume_delta=40.0,
        volume_delta_ratio=0.40,
        buy_value=7000.0,
        sell_value=3000.0,
        value_delta=4000.0,
        price_change_pct=1.0,
        algopack_oi_open=1000.0,
        algopack_oi_close=1100.0,
        algopack_oi_change=100.0,
        individuals=ClientOpenInterest("FIZ", 200.0, 700.0, -500.0, 10, 8, "2026-10-01T18:45:00+03:00"),
        legal_entities=ClientOpenInterest("YUR", -200.0, 300.0, -500.0, 5, 7, "2026-10-01T18:45:00+03:00"),
        data_quality="PASS",
        warnings=(),
    )


def test_method_set_exposes_independent_signals() -> None:
    snapshot = MarketSnapshot(
        symbol="Si",
        secid="SiZ6",
        as_of="2026-10-01T18:45:00+03:00",
        source="MOEX_ISS",
        d1=_state("D1", trend=3.0, r5=0.02, r20=0.05, atr=0.012),
        h1=_state("H1", trend=2.0, r5=0.01, r20=0.03),
        m15=_state("M15", trend=1.0, r5=0.005, r20=0.02),
        data_quality="PASS",
        warnings=(),
        flow=_flow(),
    )

    signals = {item.name: item for item in build_method_signals(snapshot)}

    assert set(signals) == {
        "TREND_MOMENTUM",
        "TIMEFRAME_ALIGNMENT",
        "HORIZON_MOMENTUM",
        "HORIZON_REVERSION",
        "REGIME_TREND",
        "MEAN_REVERSION",
        "VOLATILITY_REGIME",
        "VOLUME_LEVELS",
        "FLOW_OI",
    }
    assert signals["TREND_MOMENTUM"].direction == "UP"
    assert signals["TIMEFRAME_ALIGNMENT"].direction == "UP"
    assert signals["REGIME_TREND"].direction == "UP"
    assert signals["MEAN_REVERSION"].available is False
    assert signals["VOLUME_LEVELS"].direction == "UP"
    assert signals["FLOW_OI"].direction == "UP"
    assert signals["VOLATILITY_REGIME"].role == "CONTEXT"
    assert signals["VOLATILITY_REGIME"].context_state == "NORMAL_VOL+EFFICIENT_TREND"


def test_missing_flow_is_unavailable_not_zero_signal() -> None:
    snapshot = MarketSnapshot(
        symbol="IMOEX",
        secid="IMOEX",
        as_of="2026-10-01T18:45:00+03:00",
        source="MOEX_ISS",
        d1=_state("D1", trend=0.0, r5=0.0, r20=0.0, atr=0.01),
        h1=_state("H1", trend=0.0, r5=0.0, r20=0.0),
        m15=_state("M15", trend=0.0, r5=0.0, r20=0.0),
        data_quality="PASS",
        warnings=(),
        flow=None,
    )

    flow = {item.name: item for item in build_method_signals(snapshot)}["FLOW_OI"]

    assert flow.available is False
    assert flow.score is None
    assert flow.direction == "UNAVAILABLE"



def test_choppy_stretched_market_enables_mean_reversion_only() -> None:
    snapshot = MarketSnapshot(
        symbol="SBER",
        secid="SBER",
        as_of="2026-10-01T18:45:00+03:00",
        source="MOEX_ISS",
        d1=_state(
            "D1", trend=1.0, r5=0.03, r20=0.01, atr=0.012,
            er=0.10, close=104.0, vwap=100.0, location=0.90,
        ),
        h1=_state(
            "H1", trend=1.0, r5=0.02, r20=0.01,
            er=0.15, close=103.0, vwap=100.0, location=0.85,
        ),
        m15=_state(
            "M15", trend=0.5, r5=0.01, r20=0.005,
            er=0.15, close=102.0, vwap=100.0, location=0.75,
        ),
        data_quality="PASS",
        warnings=(),
        flow=None,
    )

    signals = {item.name: item for item in build_method_signals(snapshot)}

    assert signals["REGIME_TREND"].available is False
    assert signals["REGIME_TREND"].direction == "UNAVAILABLE"
    assert signals["MEAN_REVERSION"].available is True
    assert signals["MEAN_REVERSION"].direction == "DOWN"
    assert signals["MEAN_REVERSION"].score is not None
    assert signals["MEAN_REVERSION"].score < 0


def test_mixed_regime_abstains_from_both_regime_methods() -> None:
    snapshot = MarketSnapshot(
        symbol="SBER",
        secid="SBER",
        as_of="2026-10-01T18:45:00+03:00",
        source="MOEX_ISS",
        d1=_state(
            "D1", trend=2.0, r5=0.01, r20=0.02, atr=0.012,
            er=0.36, close=102.0, vwap=100.0, location=0.70,
        ),
        h1=_state("H1", trend=1.0, r5=0.01, r20=0.02),
        m15=_state("M15", trend=1.0, r5=0.01, r20=0.02),
        data_quality="PASS",
        warnings=(),
        flow=None,
    )

    signals = {item.name: item for item in build_method_signals(snapshot)}

    assert signals["REGIME_TREND"].available is False
    assert signals["MEAN_REVERSION"].available is False



def test_horizon_methods_can_disagree_across_5_10_20_sessions() -> None:
    snapshot = MarketSnapshot(
        symbol="SBER",
        secid="SBER",
        as_of="2026-10-01T18:45:00+03:00",
        source="MOEX_ISS",
        d1=_state(
            "D1",
            trend=0.0,
            r5=0.02,
            r10=-0.03,
            r20=0.05,
            atr=0.012,
            er=0.35,
        ),
        h1=_state("H1", trend=0.0, r5=0.0, r20=0.0),
        m15=_state("M15", trend=0.0, r5=0.0, r20=0.0),
        data_quality="PASS",
        warnings=(),
        flow=None,
    )

    signals = {item.name: item for item in build_method_signals(snapshot)}
    momentum = dict(signals["HORIZON_MOMENTUM"].horizon_scores)
    reversion = dict(signals["HORIZON_REVERSION"].horizon_scores)

    assert momentum[5] > 0
    assert momentum[10] < 0
    assert momentum[20] > 0
    assert reversion[5] < 0
    assert reversion[10] > 0
    assert reversion[20] < 0
