"""Causal Market Snapshot builder from real MOEX candles and optional flow data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from birzha.application.flow import MarketFlowService
from birzha.application.market_data import MOEX_TIMEZONE, MarketDataService
from birzha.domain.flow import MarketFlowSnapshot
from birzha.domain.market import Candle, CandleSeries
from birzha.domain.snapshot import MarketSnapshot, TimeframeState
from birzha.providers.moex_analytics import MoexAnalyticsClient


@dataclass(slots=True)
class MarketSnapshotService:
    market_data: MarketDataService
    flow: MarketFlowService | None = None

    @classmethod
    def default(cls) -> "MarketSnapshotService":
        market_data = MarketDataService.default()
        return cls(
            market_data=market_data,
            flow=MarketFlowService(
                market_data=market_data,
                analytics=MoexAnalyticsClient(),
            ),
        )

    def build(self, symbol: str, *, as_of_date: str | None = None) -> MarketSnapshot:
        till = date.fromisoformat(as_of_date) if as_of_date else datetime.now(MOEX_TIMEZONE).date()
        instrument = self.market_data.resolve(symbol)

        d1 = self.market_data.candles_for_instrument(
            instrument,
            timeframe="D1",
            from_date=(till - timedelta(days=260)).isoformat(),
            till_date=till.isoformat(),
            completed_only=True,
        )
        h1 = self.market_data.candles_for_instrument(
            instrument,
            timeframe="H1",
            from_date=(till - timedelta(days=60)).isoformat(),
            till_date=till.isoformat(),
            completed_only=True,
        )
        m15 = self.market_data.candles_for_instrument(
            instrument,
            timeframe="M15",
            from_date=(till - timedelta(days=20)).isoformat(),
            till_date=till.isoformat(),
            completed_only=True,
        )

        last_ends = [series.candles[-1].end for series in (d1, h1, m15) if series.candles]
        if not last_ends:
            raise ValueError("no completed candles available for snapshot")

        # T0 is the latest completed observation available to the system. Each
        # timeframe may legitimately end earlier (e.g. D1 vs current H1/M15);
        # causal correctness requires every included observation <= T0, not that
        # all timeframes be artificially truncated to the oldest last bar.
        causal_t0 = max(last_ends, key=_timestamp)
        d1 = _cut_at(d1, causal_t0)
        h1 = _cut_at(h1, causal_t0)
        m15 = _cut_at(m15, causal_t0)

        warnings: list[str] = []
        minimums = {"D1": 50, "H1": 50, "M15": 50}
        for series in (d1, h1, m15):
            if series.count < minimums[series.timeframe]:
                warnings.append(f"{series.timeframe}: insufficient_history={series.count}")

        flow_snapshot: MarketFlowSnapshot | None = None
        if self.flow is not None:
            flow_snapshot = self.flow.build_for_instrument(
                instrument,
                till_date=till.isoformat(),
                lookback_days=5,
                cutoff_at=causal_t0,
            )
            if flow_snapshot.secid != instrument.secid:
                raise ValueError("flow contract does not match candle contract")
            if flow_snapshot.as_of is not None and _timestamp(flow_snapshot.as_of) > _timestamp(causal_t0):
                raise ValueError("flow data is later than Market Snapshot T0")
            warnings.extend(f"FLOW:{warning}" for warning in flow_snapshot.warnings)

        quality = "PASS" if not warnings else "DEGRADED"
        source = "MOEX_ISS+ALGOPACK+FUTOI" if flow_snapshot is not None else "MOEX_ISS"
        return MarketSnapshot(
            symbol=symbol,
            secid=instrument.secid,
            as_of=causal_t0,
            source=source,
            d1=_state(d1),
            h1=_state(h1),
            m15=_state(m15),
            flow=flow_snapshot,
            data_quality=quality,
            warnings=tuple(warnings),
        )


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=MOEX_TIMEZONE)
    return parsed.astimezone(MOEX_TIMEZONE)


def _cut_at(series: CandleSeries, t0: str) -> CandleSeries:
    boundary = _timestamp(t0)
    candles = tuple(
        candle
        for candle in series.candles
        if candle.completed and _timestamp(candle.end) <= boundary
    )
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
