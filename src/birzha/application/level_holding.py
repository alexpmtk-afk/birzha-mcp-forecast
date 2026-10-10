"""Consecutive close facts conditional on a separately supplied expected schedule."""
import hashlib
import json
from datetime import timedelta, timezone
from itertools import groupby
from birzha.application.level_reaction import build_level_reaction_report, canonical_bytes, _receipt_time
from birzha.domain.price_levels import evidence_time

HOLDING_FACTS_VERSION = "LEVEL_HOLDING_FACTS_V1"
SCHEDULE_VERSION = "EXPECTED_BAR_SCHEDULE_V1"


def _unique_pairs(items):
    result = {}
    for key,value in items:
        if key in result:
            raise ValueError("duplicate schedule JSON field")
        result[key] = value
    return result


def _continuity(series, *, schedule_bytes, anchor, cutoff):
    actual = [(evidence_time(c.begin),evidence_time(c.end)) for c in series.candles]
    if schedule_bytes is None:
        return {"status":"UNAVAILABLE", "reason":"EXPECTED_SCHEDULE_MISSING", "expected_count":None, "actual_count":len(actual), "missing":None, "unexpected":None, "schedule_sha256":None}
    if not isinstance(schedule_bytes, bytes):
        raise ValueError("schedule evidence requires original bytes")
    schedule = json.loads(schedule_bytes, object_pairs_hook=_unique_pairs)
    if schedule.get("version") != SCHEDULE_VERSION or schedule.get("origin") != "EXTERNAL_EXPECTED_SCHEDULE" or schedule.get("complete") is not True:
        raise ValueError("complete external expected schedule required")
    if schedule.get("symbol") != series.instrument.symbol or schedule.get("secid") != series.instrument.secid or schedule.get("timeframe") != series.timeframe:
        raise ValueError("expected schedule instrument/timeframe mismatch")
    if not isinstance(schedule.get("source"),str) or not schedule["source"]:
        raise ValueError("expected schedule source is missing")
    seen = _receipt_time(schedule.get("observed_at"))
    start,end = evidence_time(schedule.get("window_start")),evidence_time(schedule.get("window_end"))
    if start < anchor or end <= start or end > cutoff or seen > cutoff:
        raise ValueError("schedule window or receipt exceeds observation cutoff")
    slots = schedule.get("expected_bars")
    if not isinstance(slots,list) or len(slots)>5000:
        raise ValueError("expected schedule requires a bounded slot list")
    expected, previous = [], None
    for slot in slots:
        begin,finish = evidence_time(slot.get("begin")),evidence_time(slot.get("end"))
        if begin < start or finish > end or finish <= begin or previous is not None and begin <= previous:
            raise ValueError("expected schedule slots must be ordered, unique and inside its window")
        expected.append((begin,finish));previous=finish
    if any(begin < start or finish > end for begin,finish in actual):
        raise ValueError("supplied candle falls outside declared schedule window")
    def key(interval):
        if series.timeframe == "D1":
            return interval[0].astimezone(timezone(timedelta(hours=3))).date().isoformat()
        return interval
    actual_keys, expected_keys = [key(i) for i in actual], [key(i) for i in expected]
    actual_set, expected_set = set(actual_keys), set(expected_keys)
    if len(actual_set) != len(actual) or len(expected_set) != len(expected):
        raise ValueError("duplicate trading session in expected or actual daily series")
    missing = [slots[i] for i,key in enumerate(expected_keys) if key not in actual_set]
    unexpected = [{"begin":c.begin,"end":c.end} for c,key in zip(series.candles,actual_keys) if key not in expected_set]
    status = "UNAVAILABLE" if missing or unexpected else "MATCHES_DECLARED_SCHEDULE"
    reason = "MISSING_EXPECTED_BARS" if missing else "UNEXPECTED_BARS" if unexpected else None
    return {"status":status,"reason":reason,"expected_count":len(expected),"actual_count":len(actual),"missing":missing,"unexpected":unexpected,"schedule_sha256":hashlib.sha256(schedule_bytes).hexdigest(),"matching_identity":"MOEX_SESSION_DATE" if series.timeframe == "D1" else "EXACT_INTRADAY_INTERVAL", "schedule_source":schedule["source"],"schedule_observed_at":schedule["observed_at"],"window_start":schedule["window_start"],"window_end":schedule["window_end"],"independent_calendar_authentication":"NOT_ESTABLISHED_BY_THIS_MODULE"}


def build_level_holding_report(forecast_payload, series, *, observation_at, schedule_bytes=None):
    reaction = build_level_reaction_report(forecast_payload,series,observation_at=observation_at)
    continuity = _continuity(series,schedule_bytes=schedule_bytes,anchor=evidence_time(reaction["level_fixed_at"]),cutoff=_receipt_time(observation_at))
    levels = []
    for level in reaction["levels"]:
        ready = continuity["status"] == "MATCHES_DECLARED_SCHEDULE" and level["status"] in {"AVAILABLE","AVAILABLE_APPROXIMATE"}
        runs = []
        if ready:
            for side,group in groupby(level["bars"],key=lambda b:b["close_side"]):
                bars = list(group)
                first,last = bars[0]["end"],bars[-1]["end"]
                runs.append({"side":side,"bar_count":len(bars),"first_close_at":first,"last_close_at":last,"close_to_close_elapsed_seconds":(evidence_time(last)-evidence_time(first)).total_seconds()})
        levels.append({"level_name":level["level_name"],"price":level["price"],"source_status":level["source_status"],"status":level["status"] if ready else "UNAVAILABLE","reason":None if ready else continuity["reason"] or level["reason"],"close_runs":runs if ready else None,"longest_close_runs":{side:max((r["bar_count"] for r in runs if r["side"]==side),default=0) for side in ("ABOVE","ON","BELOW")} if ready else None,"trailing_close_run":runs[-1] if runs else None,"full_range_bar_counts":{"ABOVE":sum(b["low"]>level["price"] for b in level["bars"]),"BELOW":sum(b["high"]<level["price"] for b in level["bars"])} if ready else None,"holding_strength":"UNKNOWN","control":"UNKNOWN","continuous_price_dwell_seconds":None})
    body = {"version":HOLDING_FACTS_VERSION,"forecast_id":reaction["forecast_id"],"forecast_sha256":reaction["forecast_sha256"],"snapshot_id":reaction["snapshot_id"],"source_series_sha256":reaction["source_series_sha256"],"reaction_report_id":reaction["report_id"],"symbol":reaction["symbol"],"secid":reaction["secid"],"timeframe":series.timeframe,"level_fixed_at":reaction["level_fixed_at"],"observation_at":observation_at,"continuity":continuity,"levels":levels,"validation_status":"CONSECUTIVE_CLOSE_FACTS_ONLY"}
    return {"report_id":"holding_"+hashlib.sha256(canonical_bytes(body)).hexdigest(),**body}
