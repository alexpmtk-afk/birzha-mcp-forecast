"""Unapproved Alignment15 proposal; always NO_TRADE. No existing Forecast changes.

Original Protocol08: 0..15 is factor alignment, NOT probability. Allocation
remains TBD; the weights below are an engineering proposal, not policy.
"""
from __future__ import annotations
import math
from birzha.application.level_route import assess_levels_and_route
from birzha.application.control_holding import control_holding_from_snapshot
from birzha.application.market_state_classifier import classify_market_state_from_snapshot
from birzha.domain.snapshot import MarketSnapshot
from birzha.domain.market import Instrument, CandleSeries

SCHEMA="ALIGNMENT15_RESEARCH_PROPOSAL_V1"
WEIGHTS={"market_state":2,"control":3,"location":2,"pressure":1,
         "efficiency":2,"effort_result":2,"route":3}
assert sum(WEIGHTS.values())==15


def _location(snapshot, route):
    """Read only same-source verified profile levels; candle proxies do not count."""
    entries={x["type"]:x["price"] for x in route["level_candidates"]
             if x["exact_trade_volume_profile"] is True
             and x["price_source"]=="MATCHING_FLOW_PUBLIC_TRADE_PRICE_QUANTITY"}
    if any(key not in entries for key in ("VAL","POC","VAH")):
        return None
    price=snapshot.m15.last_close
    atr=(snapshot.normalized_features.d1_atr_price_scale
         if snapshot.normalized_features else None)
    if (type(price) not in (float,int) or not math.isfinite(price)
        or type(atr) not in (float,int) or not math.isfinite(atr) or atr<=0
        or not entries["VAL"]<=entries["POC"]<=entries["VAH"]):
        return None
    if price>entries["VAH"]: return "ABOVE_VAH"
    if price<entries["VAL"]: return "BELOW_VAL"
    if abs(price-entries["POC"])<=.15*atr: return "AT_POC"
    return "INSIDE_VALUE"


def _score(side, snapshot, state, control, route, location):
    """Seven factors; missing required evidence is NULL, never fabricated 0."""
    want="BUYERS" if side=="LONG" else "SELLERS"
    trend="TREND_UP" if side=="LONG" else "TREND_DOWN"
    pressureside="BUYERS_FAVORED" if side=="LONG" else "SELLERS_FAVORED"
    points={}
    if (state["classification_status"]=="PROVISIONAL_CANDIDATE"
        and state["candidate_regime"] in ("TREND_UP","TREND_DOWN")):
        points["market_state"]=2 if state["candidate_regime"]==trend else 0
    else: points["market_state"]=None
    confidence=control["control_confidence_out_of_10_not_probability"]
    if (control["classification_status"]=="PROVISIONAL_HOLDING_CANDIDATE"
        and type(confidence) in (int,float) and math.isfinite(confidence)
        and 0<=confidence<=10):
        points["control"]=(0 if control["control"]!=want else
                           3 if confidence>=8 else 2 if confidence>=6 else 1)
    else: points["control"]=None
    loc_points=({"ABOVE_VAH":2,"INSIDE_VALUE":1,"AT_POC":0,"BELOW_VAL":0}
                if side=="LONG" else
                {"BELOW_VAL":2,"INSIDE_VALUE":1,"AT_POC":0,"ABOVE_VAH":0})
    points["location"]=loc_points.get(location)
    pressure=control["pressure_separate_from_control"]
    points["pressure"]=(1 if pressure["side"]==pressureside else 0
                        ) if pressure["status"]=="AVAILABLE" else None
    features=snapshot.normalized_features
    efficiencies=(features.d1_efficiency_20,features.h1_efficiency_20,
                  features.m15_efficiency_20) if features else ()
    if (len(efficiencies)==3 and all(type(v) in (int,float)
        and math.isfinite(v) and 0<=v<=1 for v in efficiencies)):
        mean=sum(efficiencies)/3
        points["efficiency"]=2 if mean>=.65 else 1 if mean>=.4 else 0
    else: points["efficiency"]=None
    evidence=control["reaction_holding_evidence"]
    volume=control["candle_activity"]
    if evidence and volume:
        ratio=volume["reaction_volume_over_previous_20_median"]
        points["effort_result"]=(0 if control["control"]!=want else
                                 2 if ratio>=1.25 else 1 if ratio>=1 else 0
                                 ) if type(ratio) in (int,float) and math.isfinite(ratio) and ratio>=0 else None
    else: points["effort_result"]=None
    route_points={"HIGH":3,"MEDIUM":2,"LOW":1}
    if route["classification_status"]=="PROVISIONAL_ROUTE_CANDIDATE":
        strength=route_points.get(route["route_confidence_research_only"])
        points["route"]=(strength if route["route_direction"]==(
            "UP" if side=="LONG" else "DOWN") else 0
            ) if strength is not None else None
    else: points["route"]=None
    if any(v is not None and (type(v) is not int or not 0<=v<=WEIGHTS[k])
           for k,v in points.items()):
        raise ValueError("invalid alignment research factors")
    complete=all(x is not None for x in points.values())
    return {"side":side,
            "total_score_out_of_15_not_probability":sum(points.values()) if complete else None,
            "factors":{key:{"points":points[key],"maximum":maximum}
                       for key,maximum in WEIGHTS.items()},
            "complete_proposed_scorecard":complete,
            "missing_evidence_reason_codes":[f"MISSING_{key.upper()}" for key,value in points.items() if value is None]}


def propose_alignment_and_no_trade(snapshot: MarketSnapshot, instrument: Instrument,
                                   m15: CandleSeries) -> dict:
    """Never permits a LONG/SHORT execution decision on unapproved score weights."""
    if not isinstance(snapshot,MarketSnapshot) or not isinstance(instrument,Instrument) or not isinstance(m15,CandleSeries):
        raise TypeError("exact MarketSnapshot/Instrument/CandleSeries required")
    state=classify_market_state_from_snapshot(snapshot,instrument)
    control=control_holding_from_snapshot(snapshot,instrument,m15)
    route=assess_levels_and_route(snapshot,instrument,m15)
    if any(x.get("snapshot_id")!=route["snapshot_id"] or
           x.get("secid")!=snapshot.secid or x.get("as_of")!=snapshot.as_of
           for x in (state,control)):
        raise ValueError("research engines did not agree on frozen snapshot")
    loc=_location(snapshot,route)
    scores={side:_score(side,snapshot,state,control,route,loc)
            for side in ("LONG","SHORT")}
    reasons=["ALIGNMENT15_FORMULA_TBD_USER_APPROVAL"]
    for side,score in scores.items():
        val=score["total_score_out_of_15_not_probability"]
        if val is None: reasons.append(side+"_EVIDENCE_INCOMPLETE")
        elif val<7: reasons.append(side+"_BELOW_7_NO_TRADE")
        elif val<10: reasons.append(side+"_BETWEEN_7_AND_10_REQUIRES_CONFIRMATION")
    if route["classification_status"]!="PROVISIONAL_ROUTE_CANDIDATE":
        reasons.append("CAUSAL_ROUTE_UNAVAILABLE")
    if loc is None: reasons.append("EXACT_LOCATION_UNAVAILABLE")
    return {
        "schema":SCHEMA,"snapshot_id":route["snapshot_id"],
        "secid":snapshot.secid,"symbol":snapshot.symbol,"as_of":snapshot.as_of,
        "methodology":"DRAFT_FOR_REVIEW_NOT_APPROVED",
        "score_weights_max15":WEIGHTS.copy(),
        "market_state_research":state["candidate_regime"],
        "control_research":control["control"],
        "route_research":route["route_direction"],
        "location_exact_profile_candidate":loc,
        "scorecards":scores,"selected_decision":"NO_TRADE",
        "primary_scenario":None,"alternative_scenario":None,
        "forecast_entry":None,"execution_entry":None,"stop":None,
        "targets":[],"invalidation":None,"reversal":None,
        "reason_codes":sorted(set(reasons)),
        "research_only":True,"user_scoring_formula_approved":False,
        "independent_source_and_clock_attested":False,
        "engine3_accepted":False,"scenario_or_execution_authorized":False,
        "production_authorized":False,"existing_immutable_forecast_modified":False,
    }
