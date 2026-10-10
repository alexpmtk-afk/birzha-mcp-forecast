"""Admission of baseline inputs; this is not a full Control/Route model."""
from __future__ import annotations

from dataclasses import replace

from birzha.application.price_levels import build_price_level_evidence
from birzha.application.features import _finite_price
from birzha.application.market_state import _feature_status, _timeframe_quality
from birzha.domain.snapshot import MarketSnapshot


def admit_baseline_snapshot(snapshot: MarketSnapshot) -> tuple[MarketSnapshot, tuple[str, ...], tuple[str, ...]]:
    normalized = snapshot.normalized_features
    reasons: list[str] = []
    disabled: list[str] = []
    if snapshot.quality_contract is None:
        reasons.append('DATA_QUALITY_CONTRACT_MISSING')
    if normalized is None or not _finite_price(normalized.current_price) or normalized.current_price == 0:
        reasons.append('CAUSAL_REFERENCE_PRICE_UNAVAILABLE')
    states = {'D1': snapshot.d1, 'H1': snapshot.h1, 'M15': snapshot.m15}
    quality = _timeframe_quality(snapshot)
    quality_reasons = snapshot.quality_contract.reasons if snapshot.quality_contract else ()
    if normalized is not None and (not _finite_price(normalized.d1_atr_price_scale) or normalized.d1_atr_price_scale <= 0):
        reasons.append('D1_POSITIVE_ATR_PRICE_SCALE_UNAVAILABLE')
    if not _finite_price(snapshot.d1.last_close) or not _finite_price(snapshot.d1.atr_14_pct) or snapshot.d1.atr_14_pct <= 0:
        reasons.append('D1_RAW_ATR_INPUT_UNAVAILABLE')
    if not _finite_price(snapshot.d1.trend_score):
        reasons.append('D1_BASELINE_SCORE_UNAVAILABLE')
    if snapshot.quality_contract is not None:
        items = [item for item in snapshot.quality_contract.timeframes if item.timeframe == 'D1']
        if len(items) != 1 or items[0].candles != snapshot.d1.candles:
            reasons.append('D1_QUALITY_COUNT_CONFLICT')

    def status(name: str) -> tuple[str, str | None]:
        return _feature_status(
            name, getattr(normalized, name, None), capabilities=frozenset({'CANDLES', 'VOLUME'}),
            timeframe_states=states, timeframe_quality=quality,
            profile_is_exact=normalized.profile_is_exact if normalized else None, flow=snapshot.flow, full_windows=True,
            quality_reasons=quality_reasons,
        )

    for name in ('d1_atr_price_scale', 'd1_return_5_atr', 'd1_return_20_atr', 'd1_efficiency_20'):
        availability, reason = status(name)
        if availability != 'AVAILABLE':
            reasons.append(f'{name}:{availability}:{reason}')
    for tf in ('D1', 'H1', 'M15'):
        state = states[tf]
        name = f'{tf.lower()}_return_5_atr'
        availability, _ = status(name)
        if availability != 'AVAILABLE' or not _finite_price(state.trend_score) or not _finite_price(state.return_5):
            disabled.append(tf)
            states[tf] = replace(state, trend_score=0.0, return_5=None)
    # Use a view for score admission; never rewrite the source snapshot or its id.
    profile = snapshot.volume_profile
    if profile is not None:
        available, _ = status('distance_to_poc_atr')
        known_method = normalized is not None and normalized.profile_method == profile.method and profile.method in {'PUBLIC_TRADES_PRICE_QUANTITY_V1', 'CANDLE_VOLUME_PROXY_V1', 'CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1'}
        geometry = all(_finite_price(v) for v in (profile.val, profile.poc, profile.vah)) and profile.val <= profile.poc <= profile.vah
        level_profile = build_price_level_evidence(snapshot)[-3:]
        if available not in {'AVAILABLE', 'AVAILABLE_APPROXIMATE'} or not known_method or not geometry or any(item.price is None for item in level_profile):
            profile = None
            disabled.append('PROFILE')
    admitted = replace(snapshot, d1=states['D1'], h1=states['H1'], m15=states['M15'], volume_profile=profile)
    return admitted, tuple(reasons), tuple(disabled)
