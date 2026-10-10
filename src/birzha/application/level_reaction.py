"""Observe later bars against a prior frozen forecast without revising it."""
from datetime import datetime
import hashlib
import json

from birzha.application.features import _finite_price
from birzha.domain.forecast import LEVEL_EVIDENCE_RECORD_VERSION, validate_baseline_evidence_payload
from birzha.domain.level_reaction import REACTION_FACTS_VERSION, LevelReaction, ReactionBar
from birzha.domain.market import CandleSeries
from birzha.domain.price_levels import evidence_time


def canonical_bytes(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _receipt_time(value):
    result = evidence_time(value)
    raw = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if raw.tzinfo is None:
        raise ValueError("receipt and observation timestamps require explicit timezone")
    return result


def _validated_bars(series, anchor, cutoff):
    if series.timeframe not in {"D1", "H1", "M15"}:
        raise ValueError("reaction timeframe must be D1/H1/M15")
    if len(series.candles) > 5000:
        raise ValueError("reaction window exceeds 5000 supplied bars")
    previous_end = None
    for candle in series.candles:
        if candle.completed is not True:
            raise ValueError("unfinished candle cannot establish a reaction")
        if not all(_finite_price(v) for v in (candle.open,candle.high,candle.low,candle.close)) or not candle.low <= min(candle.open,candle.close) <= max(candle.open,candle.close) <= candle.high:
            raise ValueError("invalid completed OHLC geometry")
        begin, end = evidence_time(candle.begin), evidence_time(candle.end)
        if begin < anchor or end <= anchor or end <= begin or end > cutoff:
            raise ValueError("reaction bars must begin after level fixation and end by observation cutoff")
        if previous_end is not None and begin <= previous_end:
            raise ValueError("reaction bars must be ordered, unique and nonoverlapping")
        previous_end = end
        if candle.available_at_confidence not in {"EXACT", "DOCUMENTED"}:
            raise ValueError("candle availability time is not established")
        available, seen = _receipt_time(candle.available_at), _receipt_time(candle.observed_at)
        if not end <= available <= seen <= cutoff:
            raise ValueError("candle receipt or availability contradicts observation cutoff")


def _side(price, level):
    return "ABOVE" if price > level else "BELOW" if price < level else "ON"


def build_level_reaction_report(forecast_payload: dict, series: CandleSeries, *, observation_at: str) -> dict:
    if forecast_payload.get("record_version") != LEVEL_EVIDENCE_RECORD_VERSION:
        raise ValueError("reaction requires a frozen V3 forecast with typed level origins")
    if any(not isinstance(forecast_payload.get(k), str) or not forecast_payload[k] for k in ("forecast_id", "snapshot_id", "symbol", "secid")):
        raise ValueError("frozen forecast identity is incomplete")
    validate_baseline_evidence_payload(forecast_payload)
    anchor = evidence_time(forecast_payload["created_at_t0"])
    cutoff = _receipt_time(observation_at)
    if cutoff <= anchor:
        raise ValueError("observation must be later than frozen level T0")
    if series.instrument.secid != forecast_payload["secid"] or series.instrument.symbol != forecast_payload["symbol"]:
        raise ValueError("reaction series does not match frozen exact instrument")
    _validated_bars(series, anchor, cutoff)
    results = []
    for level in forecast_payload["level_evidence"]:
        source_status = level["status"]
        if level["price"] is None or not series.candles:
            results.append(LevelReaction(level["name"],level["price"],source_status,"UNAVAILABLE",level["reason"] if level["price"] is None else "NO_SUPPLIED_COMPLETED_OBSERVATIONS",()))
            continue
        price = level["price"]
        bars, previous, initial, excursion = [], None, None, False
        for candle in series.candles:
            side = _side(candle.close, price)
            if initial is None and side != "ON":
                initial = side
            changed = f"{previous}_TO_{side}" if previous in {"ABOVE","BELOW"} and side in {"ABOVE","BELOW"} and previous != side else None
            returned = excursion and side == initial
            if returned:
                excursion = False
            elif initial is not None and side != "ON" and side != initial:
                excursion = True
            bars.append(ReactionBar(candle.begin,candle.end,candle.observed_at,candle.available_at,candle.open,candle.high,candle.low,candle.close,candle.low <= price <= candle.high,candle.high > price,candle.low < price,side,previous,changed,returned))
            previous = side
        results.append(LevelReaction(level["name"],price,source_status,source_status,None,tuple(bars)))
    body = {
        "version": REACTION_FACTS_VERSION, "forecast_id": forecast_payload["forecast_id"],
        "forecast_sha256": hashlib.sha256(canonical_bytes(forecast_payload)).hexdigest(),
        "source_series_sha256": hashlib.sha256(canonical_bytes(series.to_dict())).hexdigest(),
        "snapshot_id": forecast_payload["snapshot_id"], "symbol": forecast_payload["symbol"],
        "secid": forecast_payload["secid"], "level_fixed_at": forecast_payload["created_at_t0"],
        "observation_at": observation_at, "timeframe": series.timeframe,
        "supplied_bar_count": len(series.candles),
        "coverage": "SUPPLIED_BARS_ONLY_CALENDAR_CONTINUITY_UNPROVEN",
        "precision": "EXACT_COMPARISON_NO_TOLERANCE", "levels": [item.to_dict() for item in results],
        "validation_status": "OBSERVATION_FACTS_ONLY",
    }
    return {"report_id": "reaction_" + hashlib.sha256(canonical_bytes(body)).hexdigest(), **body}
