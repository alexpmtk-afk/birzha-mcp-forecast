"""Read-only, opt-in Protocol08 level history and obstacle-aware route research.

Only a previously evidenced Control/Holding candidate can initiate a Route.
Original support/resistance is derived from completed pre-reaction M15 candles.
Volume-profile levels require exact public-trade lineage; candle proxies are
explicitly ineligible. These are research hypotheses, not trade signals.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

from birzha.application.control_holding import (
    control_holding_from_snapshot, PREFIX_LOOKBACK,
)
from birzha.application.market_state_classifier import (
    classify_market_state_from_snapshot,
)
from birzha.application.normalized_features import EXACT_PROFILE_METHOD
from birzha.domain.market import CandleSeries, Instrument
from birzha.domain.snapshot import MarketSnapshot

LEVEL_VERSION="LEVEL_HISTORY_RESEARCH_V1"
ROUTE_VERSION="OBSTACLE_ROUTE_RESEARCH_V1"


def _number(value, *, strictly_positive=True):
    return (type(value) in (int,float) and math.isfinite(value)
            and (value>0 if strictly_positive else value>=0))


def _timestamp(t):
    value=datetime.fromisoformat(t.replace("Z","+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive history time")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00","Z")


def _historical_level(name, price, prior, atr):
    """Touch history is observed BEFORE the Control reaction, not reconstructed."""
    role="SUPPORT" if name=="PRIOR_SUPPORT" else "RESISTANCE"
    touches=[]
    cooldown=0
    for i,c in enumerate(prior):
        touched=(c.low<=price+.15*atr) if role=="SUPPORT" else (c.high>=price-.15*atr)
        confirmed=(c.close>=price+.25*atr) if role=="SUPPORT" else (c.close<=price-.25*atr)
        if touched and confirmed and i>=cooldown:
            touches.append({
                "observed_end_utc":_timestamp(c.end),
                "event":"LEVEL_TEST_HELD",
                "level_role":role,
            })
            cooldown=i+2  # at least one intervening complete bar
    n=len(touches)
    strength="WEAK" if n<=1 else "MEDIUM" if n==2 else "STRONG"
    return {
        "type":name,
        "price":price,
        "role":role,
        "strength":strength,
        "distinct_prior_test_count":n,
        "observed_test_history":touches,
        "role_flip_history_status":"NOT_ESTABLISHED_FROM_ROLLING_EXTREME",
        "price_source":"COMPLETED_M15_ROLLING_EXTREME",
        "exact_trade_volume_profile":False,
        "source_trust_independently_attested":False,
    }


def _exact_profile_levels(snapshot, instrument, series):
    """Require matching original flow profile, not a self-declared snapshot flag."""
    profile=snapshot.volume_profile
    flow=snapshot.flow
    normalized=snapshot.normalized_features
    if profile is None:
        return [],"PROFILE_MISSING"
    if (normalized is None or normalized.profile_is_exact is not True
        or normalized.profile_method!=EXACT_PROFILE_METHOD
        or profile.method!=EXACT_PROFILE_METHOD):
        return [],"APPROXIMATE_OR_UNVERIFIED_VOLUME_PROFILE_EXCLUDED"
    if (flow is None or flow.volume_profile is None
        or flow.volume_profile!=profile
        or flow.secid!=snapshot.secid or flow.symbol!=snapshot.symbol
        or flow.data_quality!="PASS" or flow.warnings
        or "VOLUME" not in instrument.data_capabilities
        or "TRADESTATS" not in instrument.data_capabilities):
        # Snapshot's "exact" boolean cannot authenticate a forged profile.
        return [],"PROFILE_OR_FLOW_PROVENANCE_NOT_CONFIRMED"
    try:
        end=datetime.fromisoformat(flow.as_of.replace("Z","+00:00"))
        t0=datetime.fromisoformat(snapshot.as_of.replace("Z","+00:00"))
        if (end.tzinfo is None or t0.tzinfo is None
            or end.utcoffset() is None or t0.utcoffset() is None
            or end>t0):
            return [],"PROFILE_FUTURE_OR_NAIVE_TIME"
    except (AttributeError,TypeError,ValueError):
        return [],"PROFILE_FUTURE_OR_NAIVE_TIME"
    # Flow evidence must cover at least the latest completed source M15 bar.
    # It remains unverified externally even when this causal guard passes.
    try:
        latest=datetime.fromisoformat(series.candles[-1].end.replace("Z","+00:00"))
        if latest.tzinfo is None or latest.utcoffset() is None:
            return [],"M15_LAST_END_NOT_VERIFIED"
        if end<latest:
            return [],"PROFILE_FLOW_PREDATES_LAST_COMPLETED_M15"
    except (AttributeError,IndexError,TypeError,ValueError):
        return [],"M15_LAST_END_NOT_VERIFIED"
    levels=[("POC",profile.poc),("VAH",profile.vah),("VAL",profile.val)]
    levels.extend(("HVN",p) for p in profile.hvn)
    levels.extend(("LVN",p) for p in profile.lvn)
    if (not all(_number(p) for _,p in levels)
        or profile.val>profile.poc or profile.poc>profile.vah
        or not _number(profile.total_volume)):
        return [],"PROFILE_LEVEL_RANGE_INVALID"
    result=[]
    for kind,p in levels:
        result.append({
            "type":kind,"price":float(p),
            "role":"POTENTIAL_OBSTACLE_NOT_CONFIRMED_HELD_LEVEL",
            "strength":"UNVERIFIED",
            "distinct_prior_test_count":None,
            "observed_test_history":[],
            "role_flip_history_status":"NO_CAUSAL_HISTORY",
            "price_source":"MATCHING_FLOW_PUBLIC_TRADE_PRICE_QUANTITY",
            "exact_trade_volume_profile":True,
            "source_trust_independently_attested":False,
        })
    return result,None


def assess_levels_and_route(
    snapshot: MarketSnapshot, instrument: Instrument, series: CandleSeries,
):
    """Recompute BOTH Control and Market State from the SAME causal source.

    Never accept a caller-crafted dictionary claiming an approved Market State.
    """
    control=control_holding_from_snapshot(snapshot,instrument,series)
    regime=classify_market_state_from_snapshot(snapshot,instrument)
    reasons=list(control["reason_codes"])
    price=snapshot.m15.last_close
    features=snapshot.normalized_features
    atr=features.d1_atr_price_scale if features is not None else None
    levels=[]
    profile_reason=None
    if control["classification_status"]=="PROVISIONAL_HOLDING_CANDIDATE":
        prior=series.candles[-(PREFIX_LOOKBACK+3):-3]
        prices=control["levels_from_previous_20_completed_bars"]
        levels=[
            _historical_level("PRIOR_SUPPORT",prices["support"],prior,atr),
            _historical_level("PRIOR_RESISTANCE",prices["resistance"],prior,atr),
        ]
        profile,profile_reason=_exact_profile_levels(snapshot,instrument,series)
        levels.extend(profile)
        if (snapshot.normalized_features is not None
            and snapshot.normalized_features.profile_is_exact is True
            and profile_reason is not None):
            reasons.append("CLAIMED_EXACT_PROFILE_FAILED_PROVENANCE")
    else:
        reasons.append("CONTROL_HOLDING_NOT_CONFIRMED")
    if (regime["snapshot_id"]!=control["snapshot_id"]
        or regime["secid"]!=snapshot.secid or regime["as_of"]!=snapshot.as_of):
        reasons.append("MARKET_STATE_SNAPSHOT_MISMATCH")
    if regime["classification_status"]!="PROVISIONAL_CANDIDATE":
        reasons.append("MARKET_STATE_NOT_PROVISIONAL")
    mode=regime["candidate_regime"]
    if mode in ("BALANCE","EVENT",None):
        reasons.append("MARKET_STATE_NOT_DIRECTIONAL_ROUTE")
    if mode in ("TREND_DOWN","DISTRIBUTION") and control["control"]=="BUYERS":
        reasons.append("CONTROL_CONTRADICTS_MARKET_STATE")
    if mode in ("TREND_UP","ACCUMULATION") and control["control"]=="SELLERS":
        reasons.append("CONTROL_CONTRADICTS_MARKET_STATE")
    obstacles=[]
    free_atr=None
    nearest=None
    route_confidence=None
    if not reasons and control["control"] is not None:
        direction="UP" if control["control"]=="BUYERS" else "DOWN"
        for level in levels:
            difference=(level["price"]-price) if direction=="UP" else (price-level["price"])
            if difference>0:
                obstacles.append({
                    "type":level["type"],
                    "price":level["price"],
                    "strength":level["strength"],
                    "distance_in_atr":round(difference/atr,6),
                    "source":level["price_source"],
                    "independently_certified":False,
                })
        obstacles.sort(key=lambda item:(item["distance_in_atr"],item["type"]))
        if not obstacles:
            reasons.append("NO_VERIFIABLE_AHEAD_OBSTACLE")
        else:
            nearest=obstacles[0]
            free_atr=nearest["distance_in_atr"]
            if free_atr<1:
                reasons.append("INSUFFICIENT_SPACE_BEFORE_OBSTACLE")
            elif free_atr>=3:
                route_confidence="HIGH"
            elif free_atr>=1.75:
                route_confidence="MEDIUM"
            else:
                route_confidence="LOW"
    result={
        "schema":ROUTE_VERSION,
        "symbol":snapshot.symbol,"secid":snapshot.secid,
        "snapshot_id":control["snapshot_id"],
        "as_of":snapshot.as_of,
        "level_history_schema":LEVEL_VERSION,
        "level_candidates":levels,
        "profile_admission_reason":profile_reason,
        "control_holding_schema":control["schema"],
        "market_state_recomputed_schema":regime["schema"],
        "market_state_recomputed_status":regime["classification_status"],
        "market_state_recomputed_regime":regime["candidate_regime"],
        "control":control["control"],
        "control_confidence_research_only":control["control_confidence_out_of_10_not_probability"],
        "control_proof_rechecked_from_source":True,
        "route_direction":(
            "UP" if control["control"]=="BUYERS" else
            "DOWN" if control["control"]=="SELLERS" else None
        ) if not reasons else None,
        "classification_status":"PROVISIONAL_ROUTE_CANDIDATE" if not reasons else "ABSTAIN",
        "typed_obstacles_ahead":obstacles,
        "nearest_obstacle":nearest,
        "free_space_atr":free_atr,
        "route_confidence_research_only":route_confidence if not reasons else None,
        "reason_codes":sorted(set(reasons)),
        "source_independently_authenticated":False,
        "profile_trade_origin_independently_certified":False,
        "route_thresholds_externally_calibrated":False,
        "level_role_flips_independently_proven":False,
        "alignment_approved":False,
        "scenario_or_trade_authorized":False,
        "production_authorized":False,
    }
    return result
