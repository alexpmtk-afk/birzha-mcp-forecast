"""Build minimal causal normalized features for Protocol 08."""

from __future__ import annotations

from dataclasses import dataclass, replace
from birzha.application.features import _finite_price
from birzha.application.market_state import feature_capability_reason, _flow_evidence_unavailable_reason
from birzha.domain.market import Instrument

from birzha.domain.flow import MarketFlowSnapshot
from birzha.domain.normalized_features import NormalizedFeatureSet, NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
from birzha.domain.snapshot import TimeframeState
from birzha.domain.volume_profile import VolumeProfileResult


EXACT_PROFILE_METHOD = "PUBLIC_TRADES_PRICE_QUANTITY_V1"


@dataclass(frozen=True, slots=True)
class NormalizedFeatureEngine:
    def build(
        self,
        *,
        d1: TimeframeState,
        h1: TimeframeState,
        m15: TimeframeState,
        flow: MarketFlowSnapshot | None,
        volume_profile: VolumeProfileResult | None,
        instrument: Instrument | None = None,
    ) -> NormalizedFeatureSet:
        current_price = _first_not_none(
            m15.last_close,
            h1.last_close,
            d1.last_close,
        )
        atr_price_scale = None
        if _finite_price(d1.last_close) and _finite_price(d1.atr_14_pct) and d1.atr_14_pct > 0:
            candidate = abs(d1.last_close) * d1.atr_14_pct
            if _finite_price(candidate) and candidate > 0:
                atr_price_scale = candidate
        session_vwap = flow.session_vwap if flow is not None else None
        oi_change_ratio = None
        if flow is not None and _finite_price(flow.algopack_oi_open) and flow.algopack_oi_open > 0:
            oi_change_ratio = _ratio(flow.algopack_oi_change, flow.algopack_oi_open)

        result = NormalizedFeatureSet(
            version=NORMALIZED_FEATURES_FULL_WINDOWS_VERSION,
            current_price=_round(current_price),
            d1_atr_price_scale=_round(atr_price_scale),
            d1_return_5_atr=_ratio(d1.return_5, d1.atr_14_pct),
            d1_return_20_atr=_ratio(d1.return_20, d1.atr_14_pct),
            h1_return_5_atr=_ratio(h1.return_5, h1.atr_14_pct),
            m15_return_5_atr=_ratio(m15.return_5, m15.atr_14_pct),
            d1_efficiency_20=_round(d1.efficiency_ratio_20),
            h1_efficiency_20=_round(h1.efficiency_ratio_20),
            m15_efficiency_20=_round(m15.efficiency_ratio_20),
            d1_price_location_20=_round(d1.price_location_20),
            h1_price_location_20=_round(h1.price_location_20),
            m15_price_location_20=_round(m15.price_location_20),
            d1_volume_ratio_20=_round(d1.volume_ratio_20),
            h1_volume_ratio_20=_round(h1.volume_ratio_20),
            m15_volume_ratio_20=_round(m15.volume_ratio_20),
            distance_to_session_vwap_atr=_distance_atr(
                current_price, session_vwap, atr_price_scale
            ),
            distance_to_poc_atr=_distance_atr(
                current_price,
                volume_profile.poc if volume_profile is not None else None,
                atr_price_scale,
            ),
            distance_to_vah_atr=_distance_atr(
                current_price,
                volume_profile.vah if volume_profile is not None else None,
                atr_price_scale,
            ),
            distance_to_val_atr=_distance_atr(
                current_price,
                volume_profile.val if volume_profile is not None else None,
                atr_price_scale,
            ),
            delta_volume_ratio=_round(
                flow.volume_delta_ratio if flow is not None else None
            ),
            oi_change_ratio=_round(oi_change_ratio),
            profile_method=volume_profile.method if volume_profile is not None else None,
            profile_is_exact=(
                volume_profile.method == EXACT_PROFILE_METHOD
                if volume_profile is not None
                else None
            ),
        )
        result = replace(result, **{
            name: None for name in ('delta_volume_ratio', 'oi_change_ratio', 'distance_to_session_vwap_atr')
            if _flow_evidence_unavailable_reason(name, flow) is not None
        })
        if instrument is not None:
            # Mask before ForecastService sees the snapshot, not only in market.state.
            masked = {
                name: None for name in result.to_dict()
                if name != 'version' and feature_capability_reason(
                    name, frozenset(instrument.data_capabilities)
                ) is not None
            }
            result = replace(result, **masked)
        return result



def _first_not_none(*values: float | None) -> float | None:
    for value in values:
        if _finite_price(value):
            return value
    return None


def _ratio(value: float | None, scale: float | None) -> float | None:
    if not _finite_price(value) or not _finite_price(scale) or scale <= 0:
        return None
    return _round(value / scale)


def _distance_atr(
    price: float | None,
    level: float | None,
    atr_price_scale: float | None,
) -> float | None:
    if not _finite_price(price) or not _finite_price(level) or not _finite_price(atr_price_scale) or atr_price_scale <= 0:
        return None
    return _round((price - level) / atr_price_scale)


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if _finite_price(value) else None
