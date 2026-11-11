"""Mathematical feature engine for causal market snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from birzha.domain.market import Candle, CandleSeries
from birzha.domain.snapshot import TimeframeState


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


def _typical_price(candle: Candle) -> float | None:
    values = [candle.high, candle.low, candle.close]
    return sum(values) / 3.0 if all(value is not None for value in values) else candle.close

def _vwap(candles: list[Candle], n: int) -> float | None:
    weighted = 0.0
    volume = 0.0
    window = candles[-n:]
    if any(c.close is None or c.volume is None for c in window):
        return None
    for candle in window:
        price = _typical_price(candle)
        if price is None or candle.volume is None or candle.volume <= 0:
            continue
        weighted += price * candle.volume
        volume += candle.volume
    return weighted / volume if volume else None


def _price_location(candles: list[Candle], n: int) -> float | None:
    window = candles[-n:]
    if any(
        c.high is None or c.low is None or c.close is None for c in window
    ):
        return None
    highs = [c.high for c in window if c.high is not None]
    lows = [c.low for c in window if c.low is not None]
    closes = [c.close for c in window if c.close is not None]
    if not highs or not lows or not closes:
        return None
    low, high = min(lows), max(highs)
    if high == low:
        return 0.5
    return (closes[-1] - low) / (high - low)


def _support(candles: list[Candle], n: int) -> float | None:
    window = candles[-n:]
    if any(c.low is None for c in window):
        return None
    lows = [c.low for c in window if c.low is not None]
    return min(lows) if lows else None


def _resistance(candles: list[Candle], n: int) -> float | None:
    window = candles[-n:]
    if any(c.high is None for c in window):
        return None
    highs = [c.high for c in window if c.high is not None]
    return max(highs) if highs else None


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
