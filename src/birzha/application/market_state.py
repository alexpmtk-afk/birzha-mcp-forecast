"""Causal Market State v0 evidence-vector construction; no regime classifier."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING

from birzha.domain.flow import MarketFlowSnapshot
from birzha.domain.market import Instrument
from birzha.domain.market_state import (
    FeatureAvailability,
    FeatureStatus,
    MarketStateVector,
)
from birzha.domain.normalized_features import NormalizedFeatureSet, NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
from birzha.application.features import _finite_price
from birzha.domain.snapshot import MarketSnapshot, TimeframeState, market_snapshot_id

if TYPE_CHECKING:
    from birzha.application.snapshot import MarketSnapshotService


_TIMEFRAMES_BY_FEATURE = {
    "d1_atr_price_scale": ("D1",),
    "d1_return_5_atr": ("D1",),
    "d1_return_20_atr": ("D1",),
    "d1_efficiency_20": ("D1",),
    "d1_price_location_20": ("D1",),
    "d1_volume_ratio_20": ("D1",),
    "h1_return_5_atr": ("H1",),
    "h1_efficiency_20": ("H1",),
    "h1_price_location_20": ("H1",),
    "h1_volume_ratio_20": ("H1",),
    "m15_return_5_atr": ("M15",),
    "m15_efficiency_20": ("M15",),
    "m15_price_location_20": ("M15",),
    "m15_volume_ratio_20": ("M15",),
    "distance_to_session_vwap_atr": ("D1",),
    "distance_to_poc_atr": ("D1",),
    "distance_to_vah_atr": ("D1",),
    "distance_to_val_atr": ("D1",),
}
_VOLUME_FEATURES = frozenset(
    {
        "d1_volume_ratio_20",
        "h1_volume_ratio_20",
        "m15_volume_ratio_20",
        "distance_to_poc_atr",
        "distance_to_vah_atr",
        "distance_to_val_atr",
        "profile_method",
        "profile_is_exact",
    }
)
_TRADESTATS_FEATURES = frozenset(
    {"distance_to_session_vwap_atr", "delta_volume_ratio"}
)
_PROFILE_VALUE_FEATURES = frozenset(
    {
        "distance_to_poc_atr",
        "distance_to_vah_atr",
        "distance_to_val_atr",
        "profile_method",
    }
)
_CANDLE_FEATURES = frozenset({"current_price", *_TIMEFRAMES_BY_FEATURE})
# Versioned lengths of the numerical inputs, not a universal timeframe guard.
_FEATURE_MINIMUMS = {
    'd1_atr_price_scale': 15,
    'd1_return_5_atr': 15, 'd1_return_20_atr': 21,
    'h1_return_5_atr': 15, 'm15_return_5_atr': 15,
    'd1_efficiency_20': 21, 'h1_efficiency_20': 21, 'm15_efficiency_20': 21,
    'd1_price_location_20': 20, 'h1_price_location_20': 20, 'm15_price_location_20': 20,
    'd1_volume_ratio_20': 21, 'h1_volume_ratio_20': 21, 'm15_volume_ratio_20': 21,
    'distance_to_session_vwap_atr': 15,
    'distance_to_poc_atr': 15, 'distance_to_vah_atr': 15, 'distance_to_val_atr': 15,
}

_MINIMUM_CANDLES = 50
_TRADESTATS_FAILURE_WARNINGS = (
    "ALGOPACK_TRADESTATS_UNAVAILABLE:",
    "ALGOPACK_TRADESTATS_EMPTY",
    "PUBLIC_TRADESTATS_CAPTURE_EMPTY",
)
_KNOWN_FLOW_WARNINGS = (
    *_TRADESTATS_FAILURE_WARNINGS,
    "FUTOI_UNAVAILABLE:",
    "FUTOI_EMPTY",
)


@dataclass(slots=True)
class MarketStateService:
    snapshots: MarketSnapshotService

    def build(
        self,
        symbol: str,
        *,
        as_of_date: str | None = None,
    ) -> dict[str, object]:
        snapshot, instrument = self.snapshots.build_with_instrument(
            symbol,
            as_of_date=as_of_date,
        )
        return build_market_state_vector(snapshot, instrument).to_dict()


def build_market_state_vector(
    snapshot: MarketSnapshot,
    instrument: Instrument,
) -> MarketStateVector:
    """Build capability-aware evidence without assigning market-state labels."""
    capabilities = frozenset(instrument.data_capabilities)
    normalized = snapshot.normalized_features or NormalizedFeatureSet()
    feature_values = normalized.to_dict()
    timeframe_states = {
        "D1": snapshot.d1,
        "H1": snapshot.h1,
        "M15": snapshot.m15,
    }
    timeframe_quality = _timeframe_quality(snapshot)
    availability: list[tuple[str, FeatureAvailability]] = []
    full_windows = normalized.version == NORMALIZED_FEATURES_FULL_WINDOWS_VERSION

    for item in fields(NormalizedFeatureSet):
        name = item.name
        if name == "version":
            continue
        status, reason = _feature_status(
            name,
            feature_values.get(name),
            capabilities=capabilities,
            timeframe_states=timeframe_states,
            timeframe_quality=timeframe_quality,
            profile_is_exact=normalized.profile_is_exact,
            flow=snapshot.flow,
            full_windows=full_windows,
            quality_reasons=snapshot.quality_contract.reasons if snapshot.quality_contract else (),
        )
        availability.append((name, FeatureAvailability(status, reason)))
        if status not in {"AVAILABLE", "AVAILABLE_APPROXIMATE"}:
            feature_values[name] = None

    timeframe_evidence = tuple(
        (
            timeframe,
            state.candles,
            *timeframe_quality.get(
                timeframe,
                (_MINIMUM_CANDLES, "DEGRADED"),
            ),
        )
        for timeframe, state in timeframe_states.items()
    )
    warnings = tuple(
        warning
        for warning in snapshot.warnings
        if "VOLUME" in capabilities or not warning.startswith("VOLUME_PROFILE:")
    )
    return MarketStateVector(
        symbol=snapshot.symbol,
        secid=snapshot.secid,
        as_of=snapshot.as_of,
        snapshot_id=market_snapshot_id(snapshot),
        snapshot_contract_version=snapshot.contract_version,
        asset_class=instrument.asset_class,
        capabilities=tuple(sorted(capabilities)),
        data_quality=snapshot.data_quality,
        classification_status="NOT_CLASSIFIED",
        features=replace(normalized, **feature_values),
        availability=tuple(availability),
        timeframe_evidence=timeframe_evidence,
        warnings=warnings,
        version="MARKET_STATE_VECTOR_V1_PER_FEATURE" if full_windows else "MARKET_STATE_VECTOR_V0",
    )


def _timeframe_quality(
    snapshot: MarketSnapshot,
) -> dict[str, tuple[int, str]]:
    if snapshot.quality_contract is not None:
        return {
            item.timeframe: (item.minimum_required, item.status)
            for item in snapshot.quality_contract.timeframes
        }
    return {
        timeframe: (
            _MINIMUM_CANDLES,
            "PASS" if state.candles >= _MINIMUM_CANDLES else "DEGRADED",
        )
        for timeframe, state in (
            ("D1", snapshot.d1),
            ("H1", snapshot.h1),
            ("M15", snapshot.m15),
        )
    }


def feature_capability_reason(name: str, capabilities: frozenset[str]) -> str | None:
    if name in _CANDLE_FEATURES and "CANDLES" not in capabilities:
        return "instrument_does_not_declare_CANDLES"
    if name in _VOLUME_FEATURES and "VOLUME" not in capabilities:
        return "instrument_does_not_declare_VOLUME"
    if name in _TRADESTATS_FEATURES and "TRADESTATS" not in capabilities:
        return "instrument_does_not_declare_TRADESTATS"
    if name == "oi_change_ratio" and not (
        {"OPEN_INTEREST", "FUTOI"} & capabilities
    ):
        return "instrument_does_not_declare_open_interest"
    return None


def _feature_status(
    name: str,
    value: object,
    *,
    capabilities: frozenset[str],
    timeframe_states: dict[str, TimeframeState],
    timeframe_quality: dict[str, tuple[int, str]],
    profile_is_exact: bool | None,
    flow: MarketFlowSnapshot | None,
    full_windows: bool = False,
    quality_reasons: tuple[str, ...] = (),
) -> tuple[FeatureStatus, str | None]:
    capability_reason = feature_capability_reason(name, capabilities)
    if capability_reason is not None:
        return "NOT_APPLICABLE", capability_reason

    required_timeframes = _TIMEFRAMES_BY_FEATURE.get(name, ())
    if name in _PROFILE_VALUE_FEATURES and profile_is_exact is False:
        required_timeframes = (*required_timeframes, "H1")
    for timeframe in required_timeframes:
        minimum, status = timeframe_quality.get(
            timeframe,
            (_MINIMUM_CANDLES, "DEGRADED"),
        )
        actual = timeframe_states[timeframe].candles
        if full_windows:
            declared_minimum = minimum
            minimum = 50 if timeframe == 'H1' and name in _PROFILE_VALUE_FEATURES and profile_is_exact is False else _FEATURE_MINIMUMS.get(name, 1)
            if actual < minimum:
                return 'INSUFFICIENT_HISTORY', f'{timeframe}_requires_{minimum}_candles'
            # Relax only the explicit general warmup limitation. Unknown quality
            # or an unrelated defect remains closed even if arithmetic exists.
            count_only = (
                status == 'DEGRADED' and actual < declared_minimum
                and bool(quality_reasons)
                and all(reason.startswith(('D1: insufficient_history=', 'H1: insufficient_history=', 'M15: insufficient_history=')) for reason in quality_reasons)
            )
            if status != 'PASS' and not count_only:
                return 'UNAVAILABLE', f'{timeframe}_quality_not_proven_for_feature'
        elif actual < minimum or status != 'PASS':
            return 'INSUFFICIENT_HISTORY', f'{timeframe}_requires_{minimum}_candles'

    if value is None:
        return "UNAVAILABLE", "causal_value_not_available_at_snapshot_T0"
    if name not in {"profile_method", "profile_is_exact"} and not _finite_price(value):
        return "UNAVAILABLE", "nonfinite_or_invalid_numeric_value"
    flow_reason = _flow_evidence_unavailable_reason(name, flow)
    if flow_reason is not None:
        return "UNAVAILABLE", flow_reason
    if name in _PROFILE_VALUE_FEATURES and profile_is_exact is False:
        return "AVAILABLE_APPROXIMATE", "candle_volume_proxy_profile"
    return "AVAILABLE", None


def _flow_evidence_unavailable_reason(
    name: str,
    flow: MarketFlowSnapshot | None,
) -> str | None:
    if name not in {
        "distance_to_session_vwap_atr",
        "delta_volume_ratio",
        "oi_change_ratio",
    }:
        return None
    if flow is None:
        return "flow_snapshot_missing"

    if name == "delta_volume_ratio":
        if flow.volume_delta_ratio is None:
            return "flow_delta_missing"
        uses_tradestats = True
    elif name == "oi_change_ratio":
        if flow.algopack_oi_open is None or flow.algopack_oi_change is None:
            return "flow_open_interest_missing"
        uses_tradestats = True
    else:
        if flow.session_vwap is None:
            return "flow_session_vwap_missing"
        if flow.session_vwap_source == "TRADESTATS_VWAP_WEIGHTED":
            uses_tradestats = True
        elif flow.session_vwap_source == "PUBLIC_TRADES_PRICE_QUANTITY":
            uses_tradestats = False
        else:
            return "flow_session_vwap_source_unknown"

    warnings = flow.warnings
    if uses_tradestats and any(
        warning.startswith(_TRADESTATS_FAILURE_WARNINGS) for warning in warnings
    ):
        return "flow_tradestats_degraded"
    if flow.data_quality not in {"PASS", "DEGRADED"}:
        return "flow_quality_unknown"
    if any(
        not warning.startswith(_KNOWN_FLOW_WARNINGS)
        for warning in warnings
    ):
        return "flow_quality_degraded_without_evidence_scope"
    if flow.data_quality == "DEGRADED" and not warnings:
        return "flow_quality_degraded_without_evidence_scope"
    return None
