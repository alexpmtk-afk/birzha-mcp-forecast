"""Causal Market Snapshot builder from real MOEX candles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from birzha.application.market_data import MarketDataService
from birzha.domain.market import Candle, CandleSeries
from birzha.domain.snapshot import MarketSnapshot, TimeframeState


@dataclass(slots=True)
class MarketSnapshotService:
    market_data: MarketDataService

    @classmethod
    def default(cls) -> "MarketSnapshotService":
        return cls(market_data=MarketDataService.default())

    def build(self, symbol: str, *, as_of_date: str | None = None) -> MarketSnapshot:
        till = date.fromisoformat(as_of_date) if as_of_date else date.today()
        instrument = self.market_data.resolve(symbol)

        d1 = self.market_data.provider.fetch_candles(
            instrument,
            timeframe="D1",
            from_date=(till - timedelta(days=260)).isoformat(),
            till_date=till.isoformat(),
            completed_only=True,
        )
        h1 = self.market_data.provider.fetch_candles(
            instrument,
            timeframe="H1",
            from_date=(till - timedelta(days=60)).isoformat(),
            till_date=till.isoformat(),
            completed_only=True,
        )
        m15 = self.market_data.provider.fetch_candles(
            instrument,
            timeframe="M15",
            from_date=(till - timedelta(days=20)).isoformat(),
            till_date=till.isoformat(),
            completed_only=True,
        )

        last_ends = [series.candles[-1].end for series in (d1, h1, m15) if series.candles]
        if not last_ends:
            raise ValueError("no completed candles available for snapshot")
        causal_t0 = min(last_ends)
        d1 = _cut_at(d1, causal_t0)
        h1 = _cut_at(h1, causal_t0)
        m15 = _cut_at(m15, causal_t0)

        warnings: list[str] = []
        minimums = {"D1": 50, "H1": 50, "M15": 50}
        for series in (d1, h1, m15):
            if series.count < minimums[series.timeframe]:
                warnings.append(f"{series.timeframe}: insufficient_history={series.count}")

        quality = "PASS" if not warnings else "DEGRADED"
        return MarketSnapshot(
            symbol=symbol,
            secid=instrument.secid,
            as_of=causal_t0,
            source="MOEX_ISS",
            d1=_state(d1),
            h1=_state(h1),
            m15=_state(m15),
            data_quality=quality,
            warnings=tuple(warnings),
        )


def _cut_at(series: CandleSeries, t0: str) -> CandleSeries:
    candles = tuple(c for c in series.candles if c.end <= t0)
    return CandleSeries(instrument=series.instrument, timeframe=series.timeframe, candles=candles)


def _state(series: CandleSeries) -> TimeframeState:
    candles = list(series.candles)
    closes = [c.close for c in candles if c.close is not None]
    volumes = [c.volume for c in candles if c.volume is not None]
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
    )


def _return_n(values: list[float], n: int) -> float | None:
    if len(values) <= n or values[-n - 1] == 0:
        return None
    return values[-1] / values[-n - 1] - 1.0


def _sma(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _efficiency_ratio(values: list[float], n: int) -> float | None:
    if len(values) <= n:
        return None
    window = values[-(n + 1):]
    direction = abs(window[-1] - window[0])
    noise = sum(abs(b - a) for a, b in zip(window, window[1:]))
    return direction / noise if noise else 0.0


def _atr_pct(candles: list[Candle], n: int) -> float | None:
    usable = [c for c in candles if c.high is not None and c.low is not None and c.close is not None]
    if len(usable) < n + 1:
        return None
    trs: list[float] = []
    for prev, cur in zip(usable[-(n + 1):-1], usable[-n:]):
        assert cur.high is not None and cur.low is not None and prev.close is not None
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    last_close = usable[-1].close
    if not last_close:
        return None
    return (sum(trs) / len(trs)) / last_close


def _volume_ratio(values: list[float], n: int) -> float | None:
    if len(values) < n + 1:
        return None
    baseline = sum(values[-(n + 1):-1]) / n
    return values[-1] / baseline if baseline else None


def _trend_score(closes: list[float]) -> float:
    if not closes:
        return 0.0
    last = closes[-1]
    score = 0.0
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    r20 = _return_n(closes, 20)
    er = _efficiency_ratio(closes, 20)
    if sma20 is not None:
        score += 1.0 if last > sma20 else -1.0
    if sma20 is not None and sma50 is not None:
        score += 1.0 if sma20 > sma50 else -1.0
    if r20 is not None:
        score += 1.0 if r20 > 0 else -1.0
    if er is not None:
        score *= 0.5 + min(1.0, er)
    return round(score, 6)
