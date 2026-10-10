"""Mathematical feature engine for causal market snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from birzha.domain.market import Candle, CandleSeries
from birzha.domain.snapshot import TimeframeState


PRICE_WINDOW_FEATURE_VERSION = "TIMEFRAME_PRICE_WINDOWS_V2"


@dataclass(slots=True)
class TimeframeFeatureEngine:
    """Convert completed candles into deterministic forecast features."""

    def build(self, series: CandleSeries) -> TimeframeState:
        candles = list(series.candles)
        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]
        return TimeframeState(
            timeframe=series.timeframe,
            candles=len(candles),
            last_close=closes[-1] if closes else None,
            return_5=_return_n(closes, 5),
            return_10=_return_n(closes, 10),
            return_20=_return_n(closes, 20),
            sma_20=_sma(closes, 20),
            sma_50=_sma(closes, 50),
            efficiency_ratio_20=_efficiency_ratio(closes, 20),
            atr_14_pct=_atr_pct(candles, 14),
            volume_ratio_20=_volume_ratio(volumes, 20),
            trend_score=_trend_score(closes),
            vwap_20=_vwap(candles, 20),
            price_location_20=_price_location(candles, 20),
            support_20=_support(candles, 20),
            resistance_20=_resistance(candles, 20),
        )


def _complete_trailing(values: list[float | None], count: int) -> list[float] | None:
    if len(values) < count:
        return None
    window = values[-count:]
    if any(value is None for value in window):
        return None
    return [value for value in window if value is not None]


def _return_n(values: list[float | None], n: int) -> float | None:
    window = _complete_trailing(values, n + 1)
    if window is None or window[0] == 0:
        return None
    return window[-1] / window[0] - 1.0


def _sma(values: list[float | None], n: int) -> float | None:
    window = _complete_trailing(values, n)
    if window is None:
        return None
    return sum(window) / n


def _efficiency_ratio(values: list[float | None], n: int) -> float | None:
    window = _complete_trailing(values, n + 1)
    if window is None:
        return None
    direction = abs(window[-1] - window[0])
    noise = sum(abs(b - a) for a, b in zip(window, window[1:]))
    return direction / noise if noise else 0.0


def _atr_pct(candles: list[Candle], n: int) -> float | None:
    window = candles[-(n + 1):]
    if len(window) < n + 1 or any(
        c.high is None or c.low is None or c.close is None for c in window
    ):
        return None
    trs: list[float] = []
    for prev, cur in zip(window[:-1], window[1:]):
        assert cur.high is not None and cur.low is not None and prev.close is not None
        trs.append(max(cur.high-cur.low, abs(cur.high-prev.close), abs(cur.low-prev.close)))
    last_close = window[-1].close
    return (sum(trs) / len(trs)) / last_close if last_close else None


def _volume_ratio(values: list[float | None], n: int) -> float | None:
    window = _complete_trailing(values, n + 1)
    if window is None:
        return None
    baseline = sum(window[:-1]) / n
    return window[-1] / baseline if baseline else None


def _finite_price(value: object) -> bool:
    try:
        return type(value) in (int, float) and isfinite(value)
    except OverflowError:
        return False


def _full_hlc_window(candles: list[Candle], n: int) -> list[Candle] | None:
    """Never backfill missing or unfinished candles with older usable rows."""
    if len(candles) < n:
        return None
    window = candles[-n:]
    for candle in window:
        if candle.completed is not True or not all(
            _finite_price(value) for value in (candle.high, candle.low, candle.close)
        ):
            return None
        if not candle.low <= candle.close <= candle.high:
            return None
    return window


def _typical_price(candle: Candle) -> float | None:
    # No close-only fallback: this is explicitly an HLC candle proxy.
    if not all(_finite_price(value) for value in (candle.high, candle.low, candle.close)):
        return None
    return sum((candle.high, candle.low, candle.close)) / 3.0


def _vwap(candles: list[Candle], n: int) -> float | None:
    window = _full_hlc_window(candles, n)
    if window is None:
        return None
    if any(not _finite_price(c.volume) or c.volume < 0 for c in window):
        return None
    # Observed zero volume is valid; unknown or negative volume is not.
    volume = 0.0
    weighted = 0.0
    for candle in window:
        price = _typical_price(candle)
        if not _finite_price(price):
            return None
        if candle.volume == 0:
            continue
        weighted += price * candle.volume
        volume += candle.volume
    if not _finite_price(volume) or volume <= 0 or not _finite_price(weighted):
        return None
    result = weighted / volume
    return result if isfinite(result) else None



def _price_location(candles: list[Candle], n: int) -> float | None:
    window = _full_hlc_window(candles, n)
    if window is None:
        return None
    low, high = min(c.low for c in window), max(c.high for c in window)
    if high == low:
        return None  # no position in a zero-width range; no invented midpoint
    width = high - low
    offset = window[-1].close - low
    if not _finite_price(width) or not _finite_price(offset):
        return None
    result = offset / width
    return result if isfinite(result) else None


def _support(candles: list[Candle], n: int) -> float | None:
    window = _full_hlc_window(candles, n)
    return min(c.low for c in window) if window is not None else None


def _resistance(candles: list[Candle], n: int) -> float | None:
    window = _full_hlc_window(candles, n)
    return max(c.high for c in window) if window is not None else None


def _trend_score(closes: list[float | None]) -> float:
    if not closes:
        return 0.0
    last = closes[-1]
    if last is None:
        return 0.0
    score = 0.0
    sma20, sma50 = _sma(closes, 20), _sma(closes, 50)
    r20, er = _return_n(closes, 20), _efficiency_ratio(closes, 20)
    if sma20 is not None: score += 1.0 if last > sma20 else -1.0
    if sma20 is not None and sma50 is not None: score += 1.0 if sma20 > sma50 else -1.0
    if r20 is not None: score += 1.0 if r20 > 0 else -1.0
    if er is not None: score *= 0.5 + min(1.0, er)
    return round(score, 6)
