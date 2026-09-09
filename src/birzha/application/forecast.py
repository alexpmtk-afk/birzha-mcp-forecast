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
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.domain.snapshot import MarketSnapshot


ENGINE_VERSION = "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS"


@dataclass(slots=True)
class ForecastService:
    snapshots: MarketSnapshotService

    @classmethod
    def default(
        cls,
        *,
        control_plane: ProcessUpstreamControlPlane | None = None,
    ) -> "ForecastService":
        shared_control = control_plane or ProcessUpstreamControlPlane()
        return cls(snapshots=MarketSnapshotService.default(control_plane=shared_control))

    def build(self, symbol: str, *, as_of_date: str | None = None) -> ForecastRecord:
        snapshot = self.snapshots.build(symbol, as_of_date=as_of_date)
        return build_forecast_from_snapshot(snapshot)


def build_forecast_from_snapshot(snapshot: MarketSnapshot) -> ForecastRecord:
    score = _combined_score(snapshot)
    strength = min(1.0, abs(score) / 5.75)
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
    reference_price = (
        snapshot.m15.last_close
        if snapshot.m15.last_close is not None
        else snapshot.h1.last_close
        if snapshot.h1.last_close is not None
        else snapshot.d1.last_close
    )

    primary, alternative, confirmation, invalidation, levels = _scenarios(snapshot, direction, reference_price)

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
        reference_price=reference_price,
        primary_scenario=primary,
        alternative_scenario=alternative,
        confirmation_level=confirmation,
        invalidation_level=invalidation,
        key_levels=levels,
    )


def _combined_score(snapshot: MarketSnapshot) -> float:
    score = 0.55 * snapshot.d1.trend_score + 0.30 * snapshot.h1.trend_score + 0.15 * snapshot.m15.trend_score
    aligned = 0
    for state in (snapshot.d1, snapshot.h1, snapshot.m15):
        if state.return_5 is not None:
            aligned += 1 if state.return_5 > 0 else -1 if state.return_5 < 0 else 0
    score += 0.25 * aligned
    er = snapshot.d1.efficiency_ratio_20
    if er is not None and er < 0.2:
        score *= 0.7
    score += _flow_adjustment(snapshot)
    score += _profile_adjustment(snapshot)
    return score


def _flow_adjustment(snapshot: MarketSnapshot) -> float:
    flow = snapshot.flow
    if flow is None:
        return 0.0
    adjustment = 0.0
    if flow.volume_delta_ratio is not None:
        clipped = max(-0.30, min(0.30, flow.volume_delta_ratio))
        adjustment += 1.5 * clipped
    price = flow.price_change_pct
    oi_open = flow.algopack_oi_open
    oi_change = flow.algopack_oi_change
    if price not in {None, 0.0} and oi_open not in {None, 0.0} and oi_change not in {None, 0.0}:
        price_sign = 1.0 if price > 0 else -1.0
        adjustment += 0.30 * price_sign if oi_change > 0 else -0.10 * price_sign
    return max(-0.75, min(0.75, adjustment))



def _profile_adjustment(snapshot: MarketSnapshot) -> float:
    profile = snapshot.volume_profile
    price = snapshot.h1.last_close
    if profile is None or price is None:
        return 0.0
    adjustment = 0.0
    if price > profile.vah:
        adjustment += 0.35
    elif price < profile.val:
        adjustment -= 0.35
    elif price > profile.poc:
        adjustment += 0.10
    elif price < profile.poc:
        adjustment -= 0.10
    if profile.shape == "P":
        adjustment += 0.10
    elif profile.shape == "b":
        adjustment -= 0.10
    return max(-0.50, min(0.50, adjustment))


def _reasons(snapshot: MarketSnapshot, score: float) -> list[str]:
    reasons = [f"combined_directional_score={score:.4f}"]
    for state in (snapshot.d1, snapshot.h1, snapshot.m15):
        reasons.append(f"{state.timeframe}:trend_score={state.trend_score:.4f},return20={_fmt(state.return_20)},er20={_fmt(state.efficiency_ratio_20)}")
    if snapshot.d1.atr_14_pct is not None:
        reasons.append(f"D1:atr14_pct={snapshot.d1.atr_14_pct * 100:.3f}")
    if snapshot.volume_profile is not None:
        p=snapshot.volume_profile
        reasons.append(f"PROFILE:poc={p.poc:.6f},vah={p.vah:.6f},val={p.val:.6f},shape={p.shape},adjustment={_profile_adjustment(snapshot):.4f},method={p.method}")
    if snapshot.flow is not None:
        flow = snapshot.flow
        reasons.append("FLOW:" f"delta_ratio={_fmt(flow.volume_delta_ratio)}," f"price_change_pct={_fmt(flow.price_change_pct)}," f"oi_change={_fmt(flow.algopack_oi_change)}," f"adjustment={_flow_adjustment(snapshot):.4f}," f"as_of={flow.as_of or 'NULL'}")
        if flow.individuals is not None or flow.legal_entities is not None:
            reasons.append("FUTOI:" f"individuals_net={_fmt(flow.individuals.net_position if flow.individuals else None)}," f"legal_net={_fmt(flow.legal_entities.net_position if flow.legal_entities else None)}")
    return reasons


def _fmt(value: float | None) -> str:
    return "NULL" if value is None else f"{value:.6f}"


def _scenarios(snapshot: MarketSnapshot, direction: str, reference_price: float | None) -> tuple[str, str, float | None, float | None, tuple[float, ...]]:
    profile = snapshot.volume_profile
    support = snapshot.h1.support_20 or snapshot.d1.support_20
    resistance = snapshot.h1.resistance_20 or snapshot.d1.resistance_20
    levels = [value for value in (
        profile.val if profile else None,
        profile.poc if profile else None,
        profile.vah if profile else None,
        support,
        resistance,
        reference_price,
    ) if value is not None]
    unique_levels = tuple(sorted(set(round(float(value), 8) for value in levels)))

    if direction == "UP":
        confirmation = profile.vah if profile is not None else resistance
        invalidation = profile.val if profile is not None else support
        primary = "Рост сохраняется при удержании цены выше зоны подтверждения; цель — продолжение движения к верхним уровням."
        alternative = "Если цена закрепится ниже уровня отмены, основной сценарий роста считается нарушенным и приоритет смещается к снижению/балансу."
    elif direction == "DOWN":
        confirmation = profile.val if profile is not None else support
        invalidation = profile.vah if profile is not None else resistance
        primary = "Снижение сохраняется при удержании цены ниже зоны подтверждения; цель — продолжение движения к нижним уровням."
        alternative = "Если цена закрепится выше уровня отмены, основной сценарий снижения считается нарушенным и приоритет смещается к росту/балансу."
    else:
        confirmation = resistance
        invalidation = support
        primary = "Базовый сценарий — баланс без подтверждённого направленного преимущества до выхода из рабочего диапазона."
        alternative = "Альтернативный сценарий включается после устойчивого выхода за одну из границ диапазона и появления направленного контроля."
    return primary, alternative, confirmation, invalidation, unique_levels
