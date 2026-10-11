"""Independent *research* Control/Holding from completed, observable M15 bars.

Control is demonstrated by a reaction to a prior (NOT look-ahead) level and
two completed subsequent holding bars. Aggregated Delta is reported separately
as Pressure; it cannot set Control. These are not validated order-flow causes,
approved Protocol08 confidence, source authenticity, or execution signals.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from statistics import median

from birzha.domain.market import CandleSeries, Instrument
from birzha.domain.snapshot import MarketSnapshot, market_snapshot_id

VERSION = "CONTROL_HOLDING_RESEARCH_V1"
MIN_CANDLES = 50
PREFIX_LOOKBACK = 20
HOLDING_BARS = 2


def _time(value: object) -> datetime:
    if not isinstance(value,str):
        raise ValueError("missing_time")
    try:
        t=datetime.fromisoformat(value.replace("Z","+00:00"))
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError("no_timezone")
        return t.astimezone(timezone.utc)
    except (TypeError,ValueError) as exc:
        raise ValueError("invalid_offset_aware_time") from exc


def _finite(value: object, *, positive=False) -> bool:
    return type(value) in (float,int) and math.isfinite(value) and (
        value>0 if positive else value>=0
    )


def _pressure(snapshot: MarketSnapshot, end: datetime, instrument: Instrument) -> dict:
    flow=snapshot.flow
    if flow is None or "TRADESTATS" not in instrument.data_capabilities:
        return {"status":"UNAVAILABLE","side":None,"delta_ratio":None,
                "reason":"FLOW_MISSING"}
    try:
        aligned=(_time(flow.as_of)==end)
    except ValueError:
        aligned=False
    delta=flow.volume_delta_ratio
    if (flow.secid!=snapshot.secid or flow.symbol!=snapshot.symbol or
        not aligned or flow.data_quality!="PASS" or flow.warnings or
        flow.intervals<1 or type(delta) not in (int,float) or
        not math.isfinite(delta) or not -1<=delta<=1):
        return {"status":"UNAVAILABLE","side":None,"delta_ratio":None,
                "reason":"FLOW_NOT_CAUSALLY_RELIABLE"}
    return {
        "status":"AVAILABLE","side":(
            "BUYERS_FAVORED" if delta>.1 else
            "SELLERS_FAVORED" if delta<-.1 else "NEUTRAL"
        ),"delta_ratio":float(delta),
        "reason":None,
    }


def control_holding_from_snapshot(
    snapshot: MarketSnapshot, instrument: Instrument, series: CandleSeries,
) -> dict:
    """Research-only; fail closed on any unverified timestamp/lineage.

    Reaction window = last three completely observed M15 candles. Levels and
    the historical volume median use ONLY the 20 completed candles preceding
    that reaction window. No future/partial candle is admitted or backfilled.
    """
    if not isinstance(snapshot,MarketSnapshot) or not isinstance(instrument,Instrument) or not isinstance(series,CandleSeries):
        raise TypeError("strict market contracts required")
    reason=[]
    asof=None
    try:
        asof=_time(snapshot.as_of)
    except ValueError:
        reason.append("SNAPSHOT_TIME_INVALID")
    identity=(
        snapshot.secid==instrument.secid==series.instrument.secid
        and snapshot.symbol==instrument.symbol==series.instrument.symbol
        and instrument.board==series.instrument.board
        and instrument.engine==series.instrument.engine
        and instrument.market==series.instrument.market
    )
    if not identity or series.timeframe!="M15":
        reason.append("INSTRUMENT_OR_TIMEFRAME_MISMATCH")
    if "CANDLES" not in instrument.data_capabilities:
        reason.append("CANDLES_CAPABILITY_MISSING")
    if "VOLUME" not in instrument.data_capabilities:
        reason.append("VOLUME_CAPABILITY_MISSING")
    if snapshot.data_quality!="PASS" or (
        snapshot.quality_contract is not None
        and snapshot.quality_contract.status!="PASS"
    ):
        reason.append("DATA_QUALITY_NOT_PASS")
    if snapshot.quality_contract is not None:
        matches=[q for q in snapshot.quality_contract.timeframes if q.timeframe=="M15"]
        if (len(matches)!=1 or matches[0].status!="PASS"
            or matches[0].candles!=snapshot.m15.candles
            or matches[0].minimum_required>snapshot.m15.candles):
            reason.append("M15_QUALITY_CONTRACT_NOT_PASS")
    if len(series.candles)<MIN_CANDLES or len(series.candles)!=snapshot.m15.candles:
        reason.append("M15_COMPLETE_HISTORY_MISMATCH")

    candles=series.candles
    previous_end=None
    if not reason:
        for candle in candles:
            try:
                begin,end,available,observed=(
                    _time(candle.begin),_time(candle.end),
                    _time(candle.available_at),_time(candle.observed_at),
                )
                if (not begin<end<=available<=observed<=asof
                    or (previous_end is not None and begin<previous_end)
                    or candle.available_at_confidence!="EXACT"
                    or candle.completed is not True):
                    raise ValueError("future/partial/overlapping/unobserved")
                if any(not _finite(getattr(candle,k),positive=True)
                       for k in ("open","high","low","close","volume")):
                    raise ValueError("nonfinite or missing quote/volume")
                if not (candle.low<=candle.open<=candle.high and
                        candle.low<=candle.close<=candle.high):
                    raise ValueError("OHLC contradiction")
                previous_end=end
            except ValueError:
                reason.append("NONCAUSAL_OR_UNVERIFIED_M15_CANDLE")
                break
        if not reason and (
            not _finite(snapshot.m15.last_close,positive=True)
            or abs(snapshot.m15.last_close-candles[-1].close)>
            max(1e-8,snapshot.m15.last_close*1e-8)
        ):
            reason.append("SNAPSHOT_LAST_CLOSE_DIFFERS_FROM_SOURCE")
    normalized=snapshot.normalized_features
    atr=normalized.d1_atr_price_scale if normalized is not None else None
    if not _finite(atr,positive=True):
        reason.append("ATR_SCALE_UNAVAILABLE")
    pressure=_pressure(snapshot,asof,instrument) if asof is not None else {
        "status":"UNAVAILABLE","side":None,"delta_ratio":None,
        "reason":"SNAPSHOT_TIME_INVALID",
    }
    candidates=[]
    levels={"support":None,"resistance":None}
    activity=None
    if not reason:
        prior=candles[-(PREFIX_LOOKBACK+HOLDING_BARS+1):-(HOLDING_BARS+1)]
        # Exactly 20 original completed bars, ALL prior to reaction T0.
        assert len(prior)==PREFIX_LOOKBACK
        reaction,hold_a,hold_b=candles[-3:]
        support=min(c.low for c in prior)
        resistance=max(c.high for c in prior)
        levels={"support":support,"resistance":resistance}
        baseline_vol=median(c.volume for c in prior)
        if baseline_vol<=0:
            reason.append("REFERENCE_ACTIVITY_ZERO")
        else:
            ratio=reaction.volume/baseline_vol
            activity={
                "reaction_volume_over_previous_20_median":round(ratio,6),
                "evidence_type":"AGGREGATE_CANDLE_VOLUME_NOT_TAKER_DELTA",
            }
            tolerance=atr*.15
            for side,level in (("BUYERS",support),("SELLERS",resistance)):
                if side=="BUYERS":
                    touched=(level-.5*atr<=reaction.low<=level+tolerance)
                    displacement=(reaction.close-level)/atr
                    holds=(hold_a.close>=level+.25*atr and
                           hold_a.low>=level-tolerance and
                           hold_b.close>=level+.25*atr and
                           hold_b.low>=level-tolerance)
                    depth=((hold_a.close-level)/atr,(hold_b.close-level)/atr)
                else:
                    touched=(level-tolerance<=reaction.high<=level+.5*atr)
                    displacement=(level-reaction.close)/atr
                    holds=(hold_a.close<=level-.25*atr and
                           hold_a.high<=level+tolerance and
                           hold_b.close<=level-.25*atr and
                           hold_b.high<=level+tolerance)
                    depth=((level-hold_a.close)/atr,(level-hold_b.close)/atr)
                if touched and displacement>=.25 and holds:
                    reaction_points=min(4,2+2*min(displacement,1))
                    hold_points=4.0
                    # Effort->Result is volume-context, not a signed
                    # buying/selling label. Delta never changes Control.
                    activity_points=2.0 if ratio>=1.25 else (
                        1.0 if ratio>=1 else 0.0
                    )
                    confidence=round(reaction_points+hold_points+activity_points,4)
                    candidates.append({
                        "side":side,
                        "confidence_out_of_10_not_probability":confidence,
                        "level":level,
                        "reaction_end_utc":_time(reaction.end).isoformat().replace("+00:00","Z"),
                        "hold_ends_utc":[_time(c.end).isoformat().replace("+00:00","Z") for c in (hold_a,hold_b)],
                        "reaction_displacement_atr":round(displacement,6),
                        "hold_depth_atr":[round(v,6) for v in depth],
                        "reaction_points":round(reaction_points,4),
                        "holding_points":hold_points,
                        "unsigned_activity_points":activity_points,
                    })
        if not candidates and not reason:
            reason.append("NO_CONFIRMED_REACTION_AND_TWO_HOLDS")
        if len(candidates)>1:
            reason.append("BOTH_SIDES_REACTION_AMBIGUOUS")
    selected=candidates[0] if len(candidates)==1 and not reason else None
    return {
        "schema":VERSION,
        "symbol":snapshot.symbol,
        "secid":snapshot.secid,
        "snapshot_id":market_snapshot_id(snapshot) if asof is not None else None,
        "as_of":snapshot.as_of,
        "classification_status":"PROVISIONAL_HOLDING_CANDIDATE" if selected else "ABSTAIN",
        "control":selected["side"] if selected else None,
        "control_confidence_out_of_10_not_probability":(
            selected["confidence_out_of_10_not_probability"] if selected else None
        ),
        "levels_from_previous_20_completed_bars":levels,
        "reaction_holding_evidence":selected,
        "candle_activity":activity,
        "pressure_separate_from_control":pressure,
        "reason_codes":sorted(set(reason)),
        "history_candles_required":MIN_CANDLES,
        "holding_bars_required":HOLDING_BARS,
        "research_rules_only":True,
        "provider_source_independently_authenticated":False,
        "holding_confidence_externally_calibrated":False,
        "alignment_or_route_approved":False,
        "forecast_or_execution_authorized":False,
        "production_authorized":False,
    }
