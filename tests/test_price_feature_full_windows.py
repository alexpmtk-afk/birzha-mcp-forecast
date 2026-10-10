"""Full-window production price inputs; no research calibration."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from birzha.application.features import (
    TimeframeFeatureEngine, PRICE_WINDOW_FEATURE_VERSION,
    _price_location, _support, _resistance, _vwap,
)
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.domain.snapshot import MarketSnapshot

FUNCTIONS = (_price_location, _support, _resistance, _vwap)


def bars(count=20):
    start = datetime(2026, 1, 1)
    return [Candle(open=100.+i, close=100.5+i, high=101.+i, low=99.+i,
                   volume=10.+i, value=1000.+i,
                   begin=(start+timedelta(days=i)).isoformat(),
                   end=(start+timedelta(days=i, hours=23)).isoformat())
            for i in range(count)]


def build(candles, symbol="SBER", asset="equity", timeframe="D1"):
    instrument = Instrument(symbol=symbol, secid=symbol, board="TEST",
                            engine="stock", market="test", asset_class=asset)
    return TimeframeFeatureEngine().build(CandleSeries(instrument, timeframe, tuple(candles)))


@pytest.mark.parametrize("count", [0, 1, 14, 19])
def test_no_twenty_candle_inputs_from_short_windows(count):
    state = build(bars(count))
    assert state.support_20 is state.resistance_20 is state.price_location_20 is state.vwap_20 is None
    assert state.candles == count
    assert state.last_close == (None if not count else 99.5+count)


def test_full_window_matches_direct_arithmetic_and_formula():
    candles = bars(20)
    state = build(candles)
    assert state.support_20 == 99
    assert state.resistance_20 == 120
    assert state.price_location_20 == (119.5-99)/(120-99)
    expected = sum((c.high+c.low+c.close)/3 * c.volume for c in candles)/sum(c.volume for c in candles)
    assert state.vwap_20 == pytest.approx(expected)
    assert PRICE_WINDOW_FEATURE_VERSION == "TIMEFRAME_PRICE_WINDOWS_V2"


@pytest.mark.parametrize("field,bad", [
    ("high", None), ("low", None), ("close", None),
    ("high", float("nan")), ("low", float("inf")), ("close", float("-inf")),
    ("high", True), ("low", "99"), ("close", 10**1000),
    ("high", 95.), ("low", 150.), ("close", 500.),
    ("completed", False), ("completed", None), ("completed", 1),
])
def test_invalid_internal_candle_blocks_whole_price_window(field, bad):
    candles = bars(25)
    candles[12] = replace(candles[12], **{field: bad})
    for function in FUNCTIONS:
        assert function(candles, 20) is None


@pytest.mark.parametrize("bad", [None, -1., float("nan"), float("inf"), True, "10", 10**1000])
def test_invalid_volume_blocks_proxy_without_removing_row(bad):
    candles = bars(20)
    candles[7] = replace(candles[7], volume=bad)
    assert _vwap(candles, 20) is None
    assert _support(candles, 20) == 99


def test_observed_zero_volume_is_valid_and_all_zero_is_unavailable():
    candles = bars(20)
    candles[7] = replace(candles[7], volume=0.)
    expected = sum((c.high+c.low+c.close)/3*c.volume for c in candles)/sum(c.volume for c in candles)
    assert _vwap(candles, 20) == pytest.approx(expected)
    assert _vwap([replace(c, volume=0.) for c in candles], 20) is None


def test_zero_width_range_has_levels_but_no_invented_location():
    candles = [replace(c, open=100., high=100., low=100., close=100.) for c in bars(20)]
    assert _price_location(candles, 20) is None
    assert _support(candles, 20) == _resistance(candles, 20) == 100
    assert _vwap(candles, 20) == pytest.approx(100)


def test_invalid_older_data_is_not_part_of_trailing_window():
    candles = bars(21)
    candles[0] = replace(candles[0], high=None, volume=None, completed=False)
    for function in FUNCTIONS:
        assert function(candles, 20) == function(candles[1:], 20)


def test_missing_inside_window_is_not_backfilled_from_older_history():
    candles = bars(60)
    candles[45] = replace(candles[45], close=None)
    assert all(function(candles, 20) is None for function in FUNCTIONS)


@pytest.mark.parametrize("symbol,asset", [
    ("SBER","equity"),("Si","future"),("BR","future"),
    ("GOLD","future"),("IMOEX","index"),("RTSI","index"),
])
@pytest.mark.parametrize("timeframe", ["D1", "H1", "M15"])
def test_same_window_contract_across_markets_and_timeframes(symbol, asset, timeframe):
    short = build(bars(19), symbol, asset, timeframe)
    complete = build(bars(20), symbol, asset, timeframe)
    assert short.support_20 is short.resistance_20 is short.vwap_20 is None
    assert complete.support_20 == 99 and complete.resistance_20 == 120


def test_negative_prices_are_not_rejected_without_contract_requirement():
    candles = [replace(c, high=-90., low=-100., close=-95.) for c in bars(20)]
    assert _support(candles, 20) == -100
    assert _resistance(candles, 20) == -90
    assert _price_location(candles, 20) == .5
    assert _vwap(candles, 20) == pytest.approx(-95)


def test_numeric_overflow_is_unavailable_not_infinite_proxy():
    candles = [replace(c, volume=1e308) for c in bars(20)]
    assert _vwap(candles, 20) is None
    candles = [replace(c, high=1e308, low=1e307, close=1e308, volume=1e308) for c in bars(20)]
    assert _vwap(candles, 20) is None


def test_forecast_does_not_publish_twenty_candle_levels_from_short_input():
    state = build(bars(19))
    snapshot = MarketSnapshot(symbol="SBER", secid="SBER", as_of="2026-02-01T00:00:00",
                              source="SYNTHETIC", d1=state,
                              h1=replace(state,timeframe="H1"), m15=replace(state,timeframe="M15"),
                              data_quality="DEGRADED", warnings=())
    record = build_forecast_from_snapshot(snapshot)
    assert record.confirmation_level is None
    assert record.invalidation_level is None
    assert record.key_levels == (state.last_close,)


def test_overflowed_range_does_not_invent_a_zero_location():
    candles = [replace(c, high=1e308, low=-1e308, close=0.) for c in bars(20)]
    assert _price_location(candles, 20) is None
