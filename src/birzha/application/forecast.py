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

from birzha.application.forecast_admission import admit_baseline_snapshot
from birzha.application.features import _finite_price
from birzha.domain.normalized_features import NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
from birzha.application.prediction import build_prediction_contract
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.forecast import (
    FORECAST_RECORD_CONTRACT_VERSION,
    BASELINE_EVIDENCE_RECORD_VERSION,
    ForecastRecord,
    HorizonForecast,
)
from birzha.domain.snapshot import MarketSnapshot, market_snapshot_id


ENGINE_VERSION = "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS"
FULL_WINDOWS_ENGINE_VERSION = "BIRZHA_FORECAST_BASELINE_V0_6_EVIDENCE_DECISION"


@dataclass(frozen=True, slots=True)
class ForecastParameters:
    name: str = "baseline"
    d1_weight: float = 0.55
    h1_weight: float = 0.30
    m15_weight: float = 0.15
    alignment_weight: float = 0.25
    direction_threshold: float = 1.0
    low_er_threshold: float = 0.20
    low_er_multiplier: float = 0.70
    flow_weight: float = 1.0
    profile_weight: float = 1.0
    strength_scale: float = 5.75

    def to_dict(self) -> dict[str, object]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


DEFAULT_FORECAST_PARAMETERS = ForecastParameters()


@dataclass(slots=True)
class ForecastService:
    snapshots: MarketSnapshotService
    parameters: ForecastParameters = DEFAULT_FORECAST_PARAMETERS

    @classmethod
    def default(
        cls,
        *,
        control_plane: ProcessUpstreamControlPlane | None = None,
    ) -> "ForecastService":
        shared_control = control_plane or ProcessUpstreamControlPlane()
        return cls(snapshots=MarketSnapshotService.default(control_plane=shared_control))

    def build(
        self, symbol: str, *, as_of_date: str | None = None,
        knowledge_cutoff_at: str | None = None,
    ) -> ForecastRecord:
        if knowledge_cutoff_at is None:
            snapshot = self.snapshots.build(symbol, as_of_date=as_of_date)
        else:
            snapshot = self.snapshots.build(
                symbol, as_of_date=as_of_date,
                knowledge_cutoff_at=knowledge_cutoff_at,
            )
        return build_forecast_from_snapshot(snapshot, parameters=self.parameters)


def build_forecast_from_snapshot(snapshot: MarketSnapshot, *, parameters: ForecastParameters = DEFAULT_FORECAST_PARAMETERS) -> ForecastRecord:
    for name, value in parameters.to_dict().items():
        if name != 'name' and not _finite_price(value):
            raise ValueError(f'forecast parameter {name} must be finite')
    if parameters.strength_scale <= 0 or parameters.direction_threshold <= 0:
        raise ValueError('forecast scale and direction threshold must be positive')
    source_snapshot = snapshot
    evidence_contract = snapshot.normalized_features is not None and snapshot.normalized_features.version == NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
    record_version = BASELINE_EVIDENCE_RECORD_VERSION if evidence_contract else FORECAST_RECORD_CONTRACT_VERSION
    abstention_reasons: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    if evidence_contract:
        snapshot, abstention_reasons, disabled = admit_baseline_snapshot(snapshot)
    engine_version = (FULL_WINDOWS_ENGINE_VERSION if snapshot.normalized_features is not None and snapshot.normalized_features.version == NORMALIZED_FEATURES_FULL_WINDOWS_VERSION else ENGINE_VERSION)
    score = _combined_score(snapshot, parameters)
    if not _finite_price(score):
        raise ValueError("forecast score must be finite")
    if abstention_reasons:
        score = 0.0
    strength = min(1.0, abs(score) / parameters.strength_scale)
    if score >= parameters.direction_threshold:
        direction = "UP"
        control = "BUYERS"
    elif score <= -parameters.direction_threshold:
        direction = "DOWN"
        control = "SELLERS"
    else:
        direction = "NEUTRAL"
        control = "BALANCE"

    decision_status = None
    if evidence_contract:
        control = "UNKNOWN"
        decision_status = "ABSTAIN" if abstention_reasons else "BASELINE_NEUTRAL" if direction == "NEUTRAL" else "BASELINE_DIRECTIONAL_ESTIMATE"
    route = "TREND" if direction != "NEUTRAL" and strength >= 0.35 else "BALANCE"
    if evidence_contract:
        route = "UNAVAILABLE"
    reasons = _reasons(snapshot, score, parameters)
    reasons.extend(f"optional_timeframe_excluded:{tf}" if tf != "PROFILE" else "optional_profile_excluded" for tf in disabled)
    reasons.extend(f"abstain:{reason}" for reason in abstention_reasons)
    warnings = list(snapshot.warnings)
    warnings.append("baseline_engine_not_probability_calibrated")
    if evidence_contract:
        warnings.append("full_control_route_scenario_not_implemented")

    daily_atr = snapshot.d1.atr_14_pct
    sign = 1.0 if direction == "UP" else -1.0 if direction == "DOWN" else 0.0
    horizons: list[HorizonForecast] = []
    for sessions in (5, 10, 20):
        if daily_atr is None or abstention_reasons:
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

    prediction = None
    try:
        prediction = build_prediction_contract(snapshot)
    except ValueError:
        prediction = None

    snapshot_id = market_snapshot_id(source_snapshot)

    identity_payload = {
        "record_version": record_version,
        "symbol": snapshot.symbol,
        "secid": snapshot.secid,
        "t0": snapshot.as_of,
        "engine": engine_version,
        "snapshot_id": snapshot_id,
        "horizons": [5, 10, 20],
    }
    if parameters != DEFAULT_FORECAST_PARAMETERS:
        identity_payload["parameters"] = parameters.to_dict()
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
    if evidence_contract:
        primary = alternative = confirmation = invalidation = None

    return ForecastRecord(
        forecast_id=f"fcst_{digest}",
        symbol=snapshot.symbol,
        secid=snapshot.secid,
        created_at_t0=snapshot.as_of,
        engine_version=engine_version if parameters == DEFAULT_FORECAST_PARAMETERS else f"{engine_version}:CAL:{parameters.name}",
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
        record_version=record_version,
        decision_status=decision_status,
        abstention_reasons=abstention_reasons,
        snapshot_id=snapshot_id,
        snapshot_contract_version=snapshot.contract_version,
        prediction_contract_id=(
            prediction.contract_id if prediction is not None else None
        ),
        prediction_contract_version=(
            prediction.version if prediction is not None else None
        ),
    )


def _combined_score(snapshot: MarketSnapshot, parameters: ForecastParameters = DEFAULT_FORECAST_PARAMETERS) -> float:
    score = parameters.d1_weight * snapshot.d1.trend_score + parameters.h1_weight * snapshot.h1.trend_score + parameters.m15_weight * snapshot.m15.trend_score
    aligned = 0
    for state in (snapshot.d1, snapshot.h1, snapshot.m15):
        if state.return_5 is not None:
            aligned += 1 if state.return_5 > 0 else -1 if state.return_5 < 0 else 0
    score += parameters.alignment_weight * aligned
    er = snapshot.d1.efficiency_ratio_20
    if er is not None and er < parameters.low_er_threshold:
        score *= parameters.low_er_multiplier
    score += parameters.flow_weight * _flow_adjustment(snapshot)
    score += parameters.profile_weight * _profile_adjustment(snapshot)
    return score


def _flow_adjustment(snapshot: MarketSnapshot) -> float:
    flow = snapshot.flow
    if flow is None:
        return 0.0
    normalized = snapshot.normalized_features
    full_windows = normalized is not None and normalized.version == NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
    delta = normalized.delta_volume_ratio if full_windows else flow.volume_delta_ratio
    adjustment = 0.0
    if _finite_price(delta):
        clipped = max(-0.30, min(0.30, delta))
        adjustment += 1.5 * clipped
    price = flow.price_change_pct
    oi_open = flow.algopack_oi_open
    oi_change = flow.algopack_oi_change
    oi_allowed = not full_windows or normalized.oi_change_ratio is not None
    if oi_allowed and all(_finite_price(v) for v in (price, oi_open, oi_change)) and price != 0 and oi_open > 0 and oi_change != 0:
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


def _reasons(snapshot: MarketSnapshot, score: float, parameters: ForecastParameters = DEFAULT_FORECAST_PARAMETERS) -> list[str]:
    reasons = [f"combined_directional_score={score:.4f}", f"parameters={parameters.name}"]
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
    support = snapshot.h1.support_20 if snapshot.h1.support_20 is not None else snapshot.d1.support_20
    resistance = snapshot.h1.resistance_20 if snapshot.h1.resistance_20 is not None else snapshot.d1.resistance_20
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
