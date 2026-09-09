"""Causal Market Snapshot builder from real MOEX candles and optional flow data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from birzha.application.features import TimeframeFeatureEngine
from birzha.application.flow import MarketFlowService
from birzha.application.market_data import MOEX_TIMEZONE, MarketDataService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.flow import MarketFlowSnapshot
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.domain.snapshot import (
    DataQualityContract,
    MarketSnapshot,
    TimeframeQuality,
    TimeframeState,
)
from birzha.providers.moex_analytics import MoexAnalyticsClient


DATA_QUALITY_CONTRACT_VERSION = "DATA_QUALITY_CONTRACT_V2"


@dataclass(slots=True)
class MarketSnapshotService:
    market_data: MarketDataService
    flow: MarketFlowService | None = None

    @classmethod
    def default(
        cls,
        *,
        control_plane: ProcessUpstreamControlPlane | None = None,
    ) -> "MarketSnapshotService":
        shared_control = control_plane or ProcessUpstreamControlPlane()
        market_data = MarketDataService.default(control_plane=shared_control)
        return cls(
            market_data=market_data,
            flow=MarketFlowService(
                market_data=market_data,
                analytics=MoexAnalyticsClient(control_plane=shared_control),
            ),
        )

    def build(self, symbol: str, *, as_of_date: str | None = None) -> MarketSnapshot:
        till = date.fromisoformat(as_of_date) if as_of_date else datetime.now(MOEX_TIMEZONE).date()
        instrument = self.market_data.resolve(symbol, as_of=till)

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
        m15 = _load_m15(self.market_data, instrument, till)

        last_ends = [series.candles[-1].end for series in (d1, h1, m15) if series.candles]
        if not last_ends:
            raise ValueError("no completed candles available for snapshot")
        causal_t0 = max(last_ends, key=_timestamp)
        d1 = _cut_at(d1, causal_t0)
        h1 = _cut_at(h1, causal_t0)
        m15 = _cut_at(m15, causal_t0)

        warnings: list[str] = []
        minimums = {"D1": 50, "H1": 50, "M15": 50}
        timeframe_quality: list[TimeframeQuality] = []
        for series in (d1, h1, m15):
            minimum = minimums[series.timeframe]
            status = "PASS" if series.count >= minimum else "DEGRADED"
            if status != "PASS":
                warnings.append(f"{series.timeframe}: insufficient_history={series.count}")
            timeframe_quality.append(
                TimeframeQuality(
                    timeframe=series.timeframe,
                    candles=series.count,
                    minimum_required=minimum,
                    latest_completed_end=series.candles[-1].end if series.candles else None,
                    status=status,
                )
            )

        flow_snapshot: MarketFlowSnapshot | None = None
        flow_status = "NOT_REQUESTED"
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
            flow_status = flow_snapshot.data_quality
            warnings.extend(f"FLOW:{warning}" for warning in flow_snapshot.warnings)

        quality = (
            "PASS"
            if all(item.status == "PASS" for item in timeframe_quality)
            and flow_status in {"PASS", "NOT_REQUESTED"}
            and not warnings
            else "DEGRADED"
        )
        quality_contract = DataQualityContract(
            version=DATA_QUALITY_CONTRACT_VERSION,
            status=quality,
            timeframes=tuple(timeframe_quality),
            flow_status=flow_status,
            reasons=tuple(warnings),
        )
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
            quality_contract=quality_contract,
            warnings=tuple(warnings),
        )


def _load_m15(
    market_data: MarketDataService,
    instrument: Instrument,
    till: date,
) -> CandleSeries:
    series: CandleSeries | None = None
    for lookback_days in (7, 20):
        series = market_data.candles_for_instrument(
            instrument,
            timeframe="M15",
            from_date=(till - timedelta(days=lookback_days)).isoformat(),
            till_date=till.isoformat(),
            completed_only=True,
        )
        if series.count >= 50:
            return series
    assert series is not None
    return series


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
    return TimeframeFeatureEngine().build(series)


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
    window = values[-(n + 1) :]
    direction = abs(window[-1] - window[0])
    noise = sum(abs(b - a) for a, b in zip(window, window[1:]))
    return direction / noise if noise else 0.0


def _atr_pct(candles: list[Candle], n: int) -> float | None:
    usable = [c for c in candles if c.high is not None and c.low is not None and c.close is not None]
    if len(usable) < n + 1:
        return None
    trs: list[float] = []
    for prev, cur in zip(usable[-(n + 1) : -1], usable[-n:]):
        assert cur.high is not None and cur.low is not None and prev.close is not None
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    last_close = usable[-1].close
    if not last_close:
        return None
    return (sum(trs) / len(trs)) / last_close


def _volume_ratio(values: list[float], n: int) -> float | None:
    if len(values) < n + 1:
        return None
    baseline = sum(values[-(n + 1) : -1]) / n
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
