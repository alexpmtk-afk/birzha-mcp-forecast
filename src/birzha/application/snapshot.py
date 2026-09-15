"""Causal Market Snapshot builder from durable D1 plus on-demand intraday data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from birzha.application.features import TimeframeFeatureEngine
from birzha.application.flow import MarketFlowService
from birzha.application.market_data import MOEX_TIMEZONE, MarketDataService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.application.volume_profile import profile_from_candles
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
D1_ANALYSIS_LOOKBACK_DAYS = 300
D1_ANCHOR_LOOKBACK_CANDLES = 60
H1_ANCHOR_LOOKBACK_CANDLES = 160
H1_MIN_CONTEXT_DAYS = 14
M15_MIN_CONTEXT_DAYS = 5
M15_MAX_LOOKBACK_DAYS = 10


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
        """Build the snapshot in three causal stages.

        1. Load completed D1 and identify the current structural leg.
        2. Load H1 only after D1 selected the leg. If that anchor is very recent,
           include a small pre-anchor warm-up window so feature calculations can
           still satisfy the fixed quality contract without persisting H1.
        3. Build M15 from M1 only for a bounded tactical window ending at T0,
           likewise allowing a short feature warm-up before a recent H1 anchor.

        The market-data implementation decides where D1 comes from. In MCP/YDB
        runtime it is the durable D1 store; H1/M15 are always direct on-demand
        provider reads and never durable-history writes.
        """
        till = date.fromisoformat(as_of_date) if as_of_date else datetime.now(MOEX_TIMEZONE).date()
        instrument = self.market_data.resolve(symbol, as_of=till)

        # Stage 1: durable completed daily structure first.
        d1 = self.market_data.candles_for_instrument(
            instrument,
            timeframe="D1",
            from_date=(till - timedelta(days=D1_ANALYSIS_LOOKBACK_DAYS)).isoformat(),
            till_date=till.isoformat(),
            completed_only=True,
        )
        if not d1.candles:
            raise ValueError("no completed D1 candles available for snapshot")
        d1_direction = _current_leg_direction(d1)
        d1_anchor = _structural_anchor(
            d1,
            direction=d1_direction,
            max_candles=D1_ANCHOR_LOOKBACK_CANDLES,
        )

        # Stage 2: H1 is contextual and requested only after D1 selected the leg.
        # The analysis anchor stays unchanged; the earlier start is feature warm-up
        # only and remains an on-demand provider read.
        d1_anchor_date = date.fromisoformat(d1_anchor)
        h1_context_floor = till - timedelta(days=H1_MIN_CONTEXT_DAYS)
        h1_start = min(d1_anchor_date, h1_context_floor).isoformat()
        h1 = self.market_data.candles_for_instrument(
            instrument,
            timeframe="H1",
            from_date=h1_start,
            till_date=till.isoformat(),
            completed_only=True,
        )
        h1_leg = _slice_from(h1, d1_anchor)
        h1_anchor = _structural_anchor(
            h1_leg,
            direction=d1_direction,
            max_candles=H1_ANCHOR_LOOKBACK_CANDLES,
            fallback=d1_anchor,
        )

        # Stage 3: M15 is provider-side M1 aggregation for a short tactical
        # window. Never request months/years of M1 merely to populate storage.
        tactical_floor = till - timedelta(days=M15_MAX_LOOKBACK_DAYS)
        m15_context_floor = till - timedelta(days=M15_MIN_CONTEXT_DAYS)
        h1_anchor_date = date.fromisoformat(h1_anchor)
        m15_context_start = min(h1_anchor_date, m15_context_floor)
        m15_start = max(m15_context_start, tactical_floor).isoformat()
        m15 = self.market_data.candles_for_instrument(
            instrument,
            timeframe="M15",
            from_date=m15_start,
            till_date=till.isoformat(),
            completed_only=True,
        )

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
        # Keep the profile tied to the D1-selected structural leg; the H1 rows
        # before that anchor are warm-up context for feature calculations only.
        h1_profile = _slice_from(h1, d1_anchor)
        volume_profile = profile_from_candles(h1_profile, bins=24)
        if volume_profile is not None:
            warnings.append("VOLUME_PROFILE:approximate_candle_proxy")
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
            volume_profile=volume_profile,
            data_quality=quality,
            quality_contract=quality_contract,
            warnings=tuple(warnings),
        )


def _current_leg_direction(series: CandleSeries) -> str:
    """Infer current D1 leg direction from completed closes only."""
    closes = [float(c.close) for c in series.candles if c.close is not None]
    if len(closes) < 2:
        return "UP"
    recent_span = min(10, len(closes) - 1)
    reference = closes[-recent_span - 1]
    if closes[-1] > reference:
        return "UP"
    if closes[-1] < reference:
        return "DOWN"
    return "UP" if closes[-1] >= sum(closes[-min(20, len(closes)):]) / min(20, len(closes)) else "DOWN"


def _structural_anchor(
    series: CandleSeries,
    *,
    direction: str,
    max_candles: int,
    fallback: str | None = None,
) -> str:
    """Select the most recent local extreme that can anchor the current leg.

    For an upward leg we seek a recent local low; for a downward leg a recent
    local high. If no local extreme exists, use the relevant extreme of the
    bounded recent window. The result is a date string suitable for provider
    window selection; it is not persisted as a new domain field.
    """
    usable = [c for c in series.candles if c.close is not None]
    if not usable:
        if fallback is not None:
            return fallback[:10]
        raise ValueError(f"no usable {series.timeframe} candles for structural anchor")
    window = usable[-max_candles:]
    if len(window) == 1:
        return window[0].begin[:10]

    want_low = direction.upper() != "DOWN"

    def value(candle: Candle) -> float:
        candidate = candle.low if want_low else candle.high
        if candidate is None:
            assert candle.close is not None
            candidate = candle.close
        return float(candidate)

    # Prefer the most recent confirmed local extreme, but avoid choosing the
    # final candle itself because that would not provide any intraday context.
    local_indices: list[int] = []
    for index in range(1, len(window) - 1):
        current = value(window[index])
        left = value(window[index - 1])
        right = value(window[index + 1])
        if want_low and current <= left and current <= right:
            local_indices.append(index)
        if not want_low and current >= left and current >= right:
            local_indices.append(index)
    if local_indices:
        return window[local_indices[-1]].begin[:10]

    fallback_window = window[-min(20, len(window)):]
    selected = (
        min(fallback_window, key=value)
        if want_low
        else max(fallback_window, key=value)
    )
    return selected.begin[:10]


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
    return CandleSeries(
        instrument=series.instrument,
        timeframe=series.timeframe,
        candles=candles,
        source=series.source,
    )


def _slice_from(series: CandleSeries, from_date: str) -> CandleSeries:
    """Slice a fetched context series without causing another provider read."""
    boundary = date.fromisoformat(from_date[:10])
    candles = tuple(
        candle for candle in series.candles if date.fromisoformat(candle.begin[:10]) >= boundary
    )
    return CandleSeries(
        instrument=series.instrument,
        timeframe=series.timeframe,
        candles=candles,
        source=series.source,
    )


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
