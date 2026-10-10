"""Admit factual levels from an unchanged causal snapshot."""
from birzha.application.features import _finite_price
from birzha.domain.price_levels import PriceLevelEvidence, evidence_time, EXACT_PROFILE_METHOD, PROXY_PROFILE_METHODS
from birzha.domain.normalized_features import NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
from birzha.domain.snapshot import MarketSnapshot, market_snapshot_id


def _time_reason(source_end, t0):
    try:
        return "SOURCE_AFTER_T0" if evidence_time(source_end) > evidence_time(t0) else None
    except (TypeError, ValueError, OverflowError):
        return "SOURCE_TIME_UNAVAILABLE"


def _quality(snapshot, tf, minimum):
    state = getattr(snapshot, tf.lower())
    if snapshot.normalized_features is None or snapshot.normalized_features.version != NORMALIZED_FEATURES_FULL_WINDOWS_VERSION:
        return "UNAVAILABLE", "FULL_WINDOW_PRODUCER_VERSION_UNPROVEN", None
    items = [q for q in snapshot.quality_contract.timeframes if q.timeframe == tf] if snapshot.quality_contract else []
    if len(items) != 1 or items[0].candles != state.candles or state.timeframe != tf:
        return "UNAVAILABLE", "QUALITY_COUNT_OR_TIMEFRAME_CONFLICT", None
    q = items[0]
    if state.candles < minimum:
        return "INSUFFICIENT_HISTORY", f"REQUIRES_{minimum}_COMPLETED_CANDLES", q.latest_completed_end
    count_only = q.status == "DEGRADED" and state.candles < q.minimum_required and bool(snapshot.quality_contract.reasons) and all(r.startswith(("D1: insufficient_history=", "H1: insufficient_history=", "M15: insufficient_history=")) for r in snapshot.quality_contract.reasons)
    if q.status != "PASS" and not count_only:
        return "UNAVAILABLE", "SOURCE_QUALITY_UNPROVEN", q.latest_completed_end
    reason = _time_reason(q.latest_completed_end, snapshot.as_of)
    return ("UNAVAILABLE" if reason else "AVAILABLE"), reason, q.latest_completed_end


def build_price_level_evidence(snapshot: MarketSnapshot) -> tuple[PriceLevelEvidence, ...]:
    identity = market_snapshot_id(snapshot)
    result = []
    def add(name, price, method, tf, end, status, reason):
        result.append(PriceLevelEvidence(name, price if status in {"AVAILABLE", "AVAILABLE_APPROXIMATE"} else None, method, tf, end, status, reason, identity, snapshot.secid, snapshot.as_of))
    for tf in ("D1", "H1", "M15"):
        state = getattr(snapshot, tf.lower())
        status, reason, end = _quality(snapshot, tf, 20)
        if status == "AVAILABLE" and (not all(_finite_price(v) for v in (state.support_20, state.resistance_20)) or state.support_20 > state.resistance_20):
            status, reason = "UNAVAILABLE", "FULL_RANGE_UNAVAILABLE_OR_REVERSED"
        for role, value in (("RANGE_LOW_20",state.support_20),("RANGE_HIGH_20",state.resistance_20)):
            add(f"{tf}_{role}",value,"COMPLETED_HLC_RANGE_20_V1",tf,end,status,reason)
    profile = snapshot.volume_profile
    method = profile.method if profile else "UNAVAILABLE"
    tf, end = None, None
    status, reason = "UNAVAILABLE", "PROFILE_MISSING"
    if profile:
        normalized = snapshot.normalized_features
        valid = all(_finite_price(v) for v in (profile.val,profile.poc,profile.vah,profile.total_volume)) and profile.val <= profile.poc <= profile.vah and profile.total_volume > 0
        if not valid:
            reason = "PROFILE_GEOMETRY_OR_VOLUME_INVALID"
        elif normalized is None or normalized.profile_method != method or any(not _finite_price(getattr(normalized,name)) for name in ("distance_to_val_atr","distance_to_poc_atr","distance_to_vah_atr")):
            reason = "PROFILE_NORMALIZED_ADMISSION_MISSING"
        elif method in PROXY_PROFILE_METHODS and normalized.profile_is_exact is False:
            tf = "H1"
            status, reason, end = _quality(snapshot, tf, 50)
            if status == "AVAILABLE":
                status, reason = "AVAILABLE_APPROXIMATE", "CANDLE_VOLUME_PROXY"
        elif method == EXACT_PROFILE_METHOD and normalized.profile_is_exact is True:
            flow = snapshot.flow
            if flow is None or flow.secid != snapshot.secid or flow.symbol != snapshot.symbol or flow.volume_profile != profile or flow.data_quality != "PASS" or flow.warnings:
                reason = "EXACT_PROFILE_SOURCE_UNPROVEN"
            else:
                end = flow.as_of
                reason = _time_reason(end, snapshot.as_of)
                status = "UNAVAILABLE" if reason else "AVAILABLE"
        else:
            reason = "PROFILE_METHOD_UNSUPPORTED_OR_PRECISION_CONFLICT"
    for role in ("VAL","POC","VAH"):
        add(f"PROFILE_{role}",getattr(profile,role.lower()) if profile else None,method,tf,end,status,reason)
    return tuple(result)
