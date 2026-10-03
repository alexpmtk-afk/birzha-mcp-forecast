from birzha.application.normalized_features import NormalizedFeatureEngine
from birzha.domain.flow import MarketFlowSnapshot
from birzha.domain.snapshot import TimeframeState
from birzha.domain.volume_profile import VolumeBin, VolumeProfileResult


def _state(
    timeframe: str,
    *,
    last_close: float,
    return_5: float | None,
    return_20: float | None,
    atr_14_pct: float | None,
    efficiency: float | None,
    location: float | None,
    volume_ratio: float | None,
) -> TimeframeState:
    return TimeframeState(
        timeframe=timeframe,
        candles=100,
        last_close=last_close,
        return_5=return_5,
        return_10=None,
        return_20=return_20,
        sma_20=None,
        sma_50=None,
        efficiency_ratio_20=efficiency,
        atr_14_pct=atr_14_pct,
        volume_ratio_20=volume_ratio,
        trend_score=0.0,
        price_location_20=location,
    )


def _flow() -> MarketFlowSnapshot:
    return MarketFlowSnapshot(
        symbol="Si",
        secid="SiZ6",
        from_date="2026-10-01",
        till_date="2026-10-01",
        as_of="2026-10-01T18:00:00+03:00",
        source="TEST",
        intervals=10,
        buy_volume=600.0,
        sell_volume=400.0,
        volume_delta=200.0,
        volume_delta_ratio=0.2,
        buy_value=None,
        sell_value=None,
        value_delta=None,
        price_change_pct=None,
        algopack_oi_open=1000.0,
        algopack_oi_close=1100.0,
        algopack_oi_change=100.0,
        individuals=None,
        legal_entities=None,
        data_quality="PASS",
        warnings=(),
        cumulative_delta=200.0,
        number_of_trades=100,
        session_vwap=99.0,
        session_vwap_source="PUBLIC_TRADES_PRICE_QUANTITY",
    )


def _profile() -> VolumeProfileResult:
    bins = (
        VolumeBin(low=97.0, high=98.0, center=97.5, volume=100.0),
        VolumeBin(low=98.0, high=99.0, center=98.5, volume=200.0),
        VolumeBin(low=99.0, high=100.0, center=99.5, volume=300.0),
        VolumeBin(low=100.0, high=101.0, center=100.5, volume=200.0),
    )
    return VolumeProfileResult(
        poc=99.5,
        vah=101.0,
        val=98.0,
        value_area_fraction=0.70,
        hvn=(99.5,),
        lvn=(97.5,),
        shape="D",
        bins=bins,
        total_volume=800.0,
        method="PUBLIC_TRADES_PRICE_QUANTITY_V1",
    )


def test_normalized_features_scale_price_moves_and_structure_by_atr() -> None:
    engine = NormalizedFeatureEngine()
    result = engine.build(
        d1=_state(
            "D1",
            last_close=100.0,
            return_5=0.04,
            return_20=0.10,
            atr_14_pct=0.02,
            efficiency=0.4,
            location=0.75,
            volume_ratio=1.2,
        ),
        h1=_state(
            "H1",
            last_close=100.0,
            return_5=0.02,
            return_20=None,
            atr_14_pct=0.01,
            efficiency=0.5,
            location=0.65,
            volume_ratio=1.1,
        ),
        m15=_state(
            "M15",
            last_close=101.0,
            return_5=0.01,
            return_20=None,
            atr_14_pct=0.005,
            efficiency=0.6,
            location=0.80,
            volume_ratio=1.3,
        ),
        flow=_flow(),
        volume_profile=_profile(),
    )

    assert result.current_price == 101.0
    assert result.d1_atr_price_scale == 2.0
    assert result.d1_return_5_atr == 2.0
    assert result.d1_return_20_atr == 5.0
    assert result.h1_return_5_atr == 2.0
    assert result.m15_return_5_atr == 2.0
    assert result.distance_to_session_vwap_atr == 1.0
    assert result.distance_to_poc_atr == 0.75
    assert result.distance_to_vah_atr == 0.0
    assert result.distance_to_val_atr == 1.5
    assert result.delta_volume_ratio == 0.2
    assert result.oi_change_ratio == 0.1
    assert result.profile_is_exact is True
    assert result.profile_method == "PUBLIC_TRADES_PRICE_QUANTITY_V1"


def test_normalized_features_keep_missing_optional_inputs_as_none() -> None:
    engine = NormalizedFeatureEngine()
    state = _state(
        "D1",
        last_close=100.0,
        return_5=None,
        return_20=None,
        atr_14_pct=None,
        efficiency=None,
        location=None,
        volume_ratio=None,
    )
    result = engine.build(
        d1=state,
        h1=state,
        m15=state,
        flow=None,
        volume_profile=None,
    )

    assert result.d1_atr_price_scale is None
    assert result.d1_return_5_atr is None
    assert result.distance_to_session_vwap_atr is None
    assert result.distance_to_poc_atr is None
    assert result.delta_volume_ratio is None
    assert result.oi_change_ratio is None
    assert result.profile_is_exact is None
