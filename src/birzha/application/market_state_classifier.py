"""Deterministic, evidence-gated *provisional* Protocol08 Market State classifier.

Consumes the causal MarketStateVector produced from one MarketSnapshot.
Six regime names are Protocol08 candidates, not calibrated probabilities or
certified Control/Route/Forecast. Missing evidence => abstain, never impute zero.
Weights and thresholds here are a research proposal, not an approved trading
policy. Do not change old Forecast payloads or validate old outcomes with it.
"""
from __future__ import annotations

from datetime import datetime
import math

from birzha.domain.market_state import MarketStateVector

VERSION = "MARKET_STATE_RULES_RESEARCH_V1"
REGIMES = (
    "TREND_UP", "TREND_DOWN", "BALANCE",
    "ACCUMULATION", "DISTRIBUTION", "EVENT",
)
CORE = (
    "d1_return_5_atr","h1_return_5_atr","m15_return_5_atr",
    "d1_efficiency_20","h1_efficiency_20","m15_efficiency_20",
    "d1_price_location_20","h1_price_location_20","m15_price_location_20",
)
FLOW = ("d1_volume_ratio_20", "delta_volume_ratio")
EVENT_FLOW = ("d1_volume_ratio_20","h1_volume_ratio_20")
SCORE_THRESHOLD = 6.5
MARGIN_THRESHOLD = 1.25


def _bounded(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _timestamp_valid(value: str) -> bool:
    try:
        stamp=datetime.fromisoformat(value.replace("Z","+00:00"))
        return stamp.tzinfo is not None and stamp.utcoffset() is not None
    except (AttributeError,TypeError,ValueError):
        return False


def classify_market_state_vector(vector: MarketStateVector) -> dict:
    """Deterministically score only witnessed candidate states, never authorize them.

    Important: A feature carrying AVAILABLE_APPROXIMATE, NOT_APPLICABLE,
    UNKNOWN or missing status cannot serve as positive evidence.
    """
    if not isinstance(vector,MarketStateVector):
        raise TypeError("causal MarketStateVector is required")
    availability=dict(vector.availability)
    values=vector.features.to_dict()
    reasons=[]
    valid={}
    for name in set((*CORE,*FLOW,*EVENT_FLOW)):
        status=availability.get(name)
        value=values.get(name)
        if status is None or status.status!="AVAILABLE":
            valid[name]=None
            continue
        if type(value) not in (float,int) or not math.isfinite(value):
            valid[name]=None
            reasons.append(f"INVALID_NUMERIC:{name}")
            continue
        # Physically meaningful ratios/position must not be accepted when
        # malformed in the pre-existing normalized feature vector.
        if name.endswith("efficiency_20") or name.endswith("price_location_20"):
            if not 0 <= value <= 1:
                valid[name]=None
                reasons.append(f"INVALID_RANGE:{name}")
                continue
        if name.endswith("volume_ratio_20") and value < 0:
            valid[name]=None
            reasons.append(f"INVALID_RANGE:{name}")
            continue
        if name=="delta_volume_ratio" and not -1 <= value <= 1:
            valid[name]=None
            reasons.append("INVALID_RANGE:delta_volume_ratio")
            continue
        valid[name]=float(value)

    tf={name:{"candles":candles,"minimum":minimum,"status":status}
        for name,candles,minimum,status in vector.timeframe_evidence}
    for name in ("D1","H1","M15"):
        x=tf.get(name)
        if not x or x["status"]!="PASS" or x["candles"] < x["minimum"] or x["minimum"]<50:
            reasons.append(f"TIMEFRAME_NOT_COMPLETE:{name}")
    if vector.data_quality!="PASS":
        reasons.append("SNAPSHOT_QUALITY_NOT_PASS")
    if not _timestamp_valid(vector.as_of):
        reasons.append("TIME_WITHOUT_TRUSTWORTHY_OFFSET")
    if "CANDLES" not in vector.capabilities:
        reasons.append("CANDLES_NOT_SUPPORTED")
    missing=[k for k in CORE if valid.get(k) is None]
    reasons += [f"CORE_FEATURE_UNAVAILABLE:{k}" for k in missing]
    score={name:None for name in REGIMES}
    evidence={name:[] for name in REGIMES}

    if not reasons:
        returns=[valid["d1_return_5_atr"],valid["h1_return_5_atr"],
                 valid["m15_return_5_atr"]]
        efficiencies=[valid["d1_efficiency_20"],valid["h1_efficiency_20"],
                      valid["m15_efficiency_20"]]
        location=[valid["d1_price_location_20"],valid["h1_price_location_20"],
                  valid["m15_price_location_20"]]
        eff=sum(efficiencies)/3.0
        loc=sum(location)/3.0
        up=sum(_bounded(v) for v in returns)/3.0
        down=sum(_bounded(-v) for v in returns)/3.0
        aligned_up=sum(v>0 for v in returns)
        aligned_down=sum(v<0 for v in returns)
        # Directional labels are not proven Control. Require D1 and >=2 TFs.
        if returns[0]>0 and aligned_up>=2:
            score["TREND_UP"]=round(10*(.55*up+.25*eff+.20*loc),4)
            evidence["TREND_UP"]=["three_timeframe_return_atr",
                                   "efficiency","price_location"]
        if returns[0]<0 and aligned_down>=2:
            score["TREND_DOWN"]=round(10*(.55*down+.25*eff+.20*(1-loc)),4)
            evidence["TREND_DOWN"]=["three_timeframe_return_atr",
                                     "efficiency","price_location"]
        # Balance needs low displacement AND poor directional efficiency.
        displacement=sum(_bounded(abs(v)/1.5) for v in returns)/3
        score["BALANCE"]=round(10*(.45*(1-displacement)+.35*(1-eff)
                                 +.20*(1-abs(loc-.5)*2)),4)
        evidence["BALANCE"]=["low_displacement","low_efficiency","central_location"]

        dvol=valid["d1_volume_ratio_20"]
        delta=valid["delta_volume_ratio"]
        if dvol is not None and delta is not None:
            # Effort -> Result hypotheses; a single delta/volume divergence
            # can be a candidate only. It is NOT proof of absorption/control.
            high_volume=_bounded((dvol-1.0)/1.5)
            stalled=_bounded(1-abs(returns[0])/0.6)
            pressure=_bounded(abs(delta)/0.5)
            if dvol>=1.5 and returns[0]>=-0.15 and delta<=-0.15:
                score["ACCUMULATION"]=round(10*(.4*high_volume+.35*stalled+.25*pressure),4)
                evidence["ACCUMULATION"]=["high_d1_volume","negative_delta",
                                          "selling_without_d1_decline"]
            if dvol>=1.5 and returns[0]<=0.15 and delta>=0.15:
                score["DISTRIBUTION"]=round(10*(.4*high_volume+.35*stalled+.25*pressure),4)
                evidence["DISTRIBUTION"]=["high_d1_volume","positive_delta",
                                          "buying_without_d1_advance"]
        # Event means only a *price-volume anomaly*, never a news cause.
        hvol=valid["h1_volume_ratio_20"]
        if dvol is not None and hvol is not None and dvol>=2.5 and hvol>=2.0 and abs(returns[0])>=1.5:
            shock=_bounded(abs(returns[0])/3.0)
            volshock=(_bounded(dvol/4.0)+_bounded(hvol/4.0))/2
            score["EVENT"]=round(10*(.55*shock+.45*volshock),4)
            evidence["EVENT"]=["unusual_d1_h1_volume","large_d1_displacement",
                               "external_cause_not_inferred"]

    candidates=sorted(((k,v) for k,v in score.items() if v is not None),
                      key=lambda kv:(-kv[1],REGIMES.index(kv[0])))
    leader=candidates[0] if candidates else None
    runner=candidates[1] if len(candidates)>1 else None
    status="ABSTAIN"
    selected=None
    if not reasons and leader is not None:
        if leader[1]>=SCORE_THRESHOLD and (
            runner is None or leader[1]-runner[1]>=MARGIN_THRESHOLD
        ):
            status="PROVISIONAL_CANDIDATE"
            selected=leader[0]
        else:
            reasons.append("INSUFFICIENT_SCORE_OR_MARGIN")
    # Six-slot vector is explicit: non-applicable anomaly regimes remain NULL.
    return {
        "schema":VERSION,
        "symbol":vector.symbol,
        "secid":vector.secid,
        "snapshot_id":vector.snapshot_id,
        "as_of":vector.as_of,
        "market_state_vector_version":vector.version,
        "data_quality":vector.data_quality,
        "classification_status":status,
        "candidate_regime":selected,
        "regime_scores_out_of_10_not_probabilities":score,
        "feature_evidence_by_regime":evidence,
        "reason_codes":sorted(set(reasons)),
        "score_threshold":SCORE_THRESHOLD,
        "minimum_margin":MARGIN_THRESHOLD,
        "classification_research_only":True,
        "external_calendar_and_clock_verified":False,
        "independent_market_state_approved":False,
        "control_and_route_established":False,
        "scenario_or_execution_authorized":False,
        "production_authorized":False,
    }


def classify_market_state_from_snapshot(snapshot, instrument) -> dict:
    """Use the exact existing causal vector builder; no second provider read."""
    from birzha.application.market_state import build_market_state_vector
    return classify_market_state_vector(build_market_state_vector(snapshot,instrument))
