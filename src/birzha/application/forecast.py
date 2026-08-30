"""Explainable baseline Forecast Engine built on the causal Market Snapshot.

This is a deterministic engineering baseline, not yet a calibrated production
model. It exists so the end-to-end MCP path can already produce an ex-ante
forecast while historical/forward validation is built in later gates.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from birzha.application.snapshot import MarketSnapshotService
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.domain.snapshot import MarketSnapshot, TimeframeState


ENGINE_VERSION = "BIRZHA_FORECAST_BASELINE_V0_1"


@dataclass(slots=True)
class ForecastService:
    snapshots: MarketSnapshotService

    @classmethod
    def default(cls) -> "ForecastService":
        return cls(snapshots=MarketSnapshotService.default())

    def build(self, symbol: str, *, as_of_date: str | None = None) -> ForecastRecord:
        snapshot = self.snapshots.build(symbol, as_of_date=as_of_date)
        return build_forecast_from_snapshot(snapshot)


def build_forecast_from_snapshot(snapshot: MarketSnapshot) -> ForecastRecord:
    score = _combined_score(snapshot)
    strength = min(1.0, abs(score) / 5.0)
    if score >= 1.0:
        direction = "UP"
        control = "BUYERS"
    elif score <= -1.0:
        direction = "DOWN"
        control = "SELLERS"
    else:
        direction = "NEUTRAL"
        control = "BALANCE"

    route = "TREND" if direction != "NEUTRAL" and strength >= 0.35 else "BALANCE"
    reasons = _reasons(snapshot, score)
    warnings = list(snapshot.warnings)
    warnings.append("baseline_engine_not_probability_calibrated")

    daily_atr = snapshot.d1.atr_14_pct
    sign = 1.0 if direction == "UP" else -1.0 if direction == "DOWN" else 0.0
    horizons: list[HorizonForecast] = []
    for sessions in (5, 10, 20):
        if daily_atr is None:
            expected = None
            adverse = None
        else:
            scale = daily_atr * math.sqrt(sessions)
            expected = round(sign * scale * (0.45 + 0.55 * strength) * 100.0, 3)
            adverse = round(scale * max(0.35, 1.0 - 0.5 * strength) * 100.0, 3)
        horizons.append(
            HorizonForecast(
                sessions=sessions,
                direction=direction,
                signal_strength=round(strength, 4),
                expected_move_pct=expected,
                adverse_move_pct=adverse,
            )
        )

    identity_payload = {
        "symbol": snapshot.symbol,
        "secid": snapshot.secid,
        "t0": snapshot.as_of,
        "engine": ENGINE_VERSION,
        "horizons": [5, 10, 20],
    }
    digest = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]

    return ForecastRecord(
        forecast_id=f"fcst_{digest}",
        symbol=snapshot.symbol,
        secid=snapshot.secid,
        created_at_t0=snapshot.as_of,
        engine_version=ENGINE_VERSION,
        direction=direction,
        signal_strength=round(strength, 4),
        control=control,
        route=route,
        horizons=tuple(horizons),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        validation_status="UNVALIDATED_BASELINE",
    )


def _combined_score(snapshot: MarketSnapshot) -> float:
    # Long horizon leads. Intraday frames refine timing but cannot dominate D1.
    d1 = snapshot.d1.trend_score
    h1 = snapshot.h1.trend_score
    m15 = snapshot.m15.trend_score
    score = 0.55 * d1 + 0.30 * h1 + 0.15 * m15

    # Preserve the old explainable CONTROL idea using return alignment.
    aligned = 0
    for state in (snapshot.d1, snapshot.h1, snapshot.m15):
        if state.return_5 is not None:
            aligned += 1 if state.return_5 > 0 else -1 if state.return_5 < 0 else 0
    score += 0.25 * aligned

    # Penalize weak directional efficiency on the daily horizon.
    er = snapshot.d1.efficiency_ratio_20
    if er is not None and er < 0.2:
        score *= 0.7
    return score


def _reasons(snapshot: MarketSnapshot, score: float) -> list[str]:
    reasons = [f"combined_directional_score={score:.4f}"]
    for state in (snapshot.d1, snapshot.h1, snapshot.m15):
        reasons.append(
            f"{state.timeframe}:trend_score={state.trend_score:.4f},"
            f"return20={_fmt(state.return_20)},er20={_fmt(state.efficiency_ratio_20)}"
        )
    if snapshot.d1.atr_14_pct is not None:
        reasons.append(f"D1:atr14_pct={snapshot.d1.atr_14_pct * 100:.3f}")
    return reasons


def _fmt(value: float | None) -> str:
    return "NULL" if value is None else f"{value:.6f}"
