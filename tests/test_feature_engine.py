from dataclasses import replace

from birzha.application.features import TimeframeFeatureEngine
from birzha.domain.market import Candle, CandleSeries, Instrument


def test_feature_engine_builds_location_vwap_and_levels() -> None:
    instrument = Instrument(symbol="S", secid="S", board="TQBR", engine="stock", market="shares", asset_class="equity")
    candles = tuple(
        Candle(100+i, 100.5+i, 101+i, 99+i, 1000+i, 10+i, f"2026-01-01T{i%24:02d}:00:00", f"2026-01-01T{i%24:02d}:59:59")
        for i in range(60)
    )
    state = TimeframeFeatureEngine().build(CandleSeries(instrument=instrument, timeframe="D1", candles=candles))
    assert state.last_close == 159.5
    assert state.support_20 == 139
    assert state.resistance_20 == 160
    assert state.price_location_20 is not None and state.price_location_20 > 0.9
    assert state.vwap_20 is not None
    assert state.trend_score > 0


def test_feature_engine_fails_closed_when_internal_candle_values_are_missing() -> None:
    instrument = Instrument(symbol="S", secid="S", board="TQBR", engine="stock", market="shares", asset_class="equity")
    candles = [
        Candle(100+i, 100.5+i, 101+i, 99+i, 1000+i, 10+i, f"2026-01-01T{i%24:02d}:00:00", f"2026-01-01T{i%24:02d}:59:59")
        for i in range(60)
    ]
    candles[52] = replace(candles[52], close=None, volume=None)

    state = TimeframeFeatureEngine().build(
        CandleSeries(instrument=instrument, timeframe="D1", candles=tuple(candles))
    )

    assert state.last_close == 159.5
    assert state.return_5 is not None
    assert state.return_20 is None
    assert state.sma_20 is None
    assert state.efficiency_ratio_20 is None
    assert state.atr_14_pct is None
    assert state.volume_ratio_20 is None
    assert state.vwap_20 is None
    assert state.price_location_20 is None
