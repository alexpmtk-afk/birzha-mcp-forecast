"""Read-only multi-contract coordinates from supplied, versioned source evidence.

No network, storage writes, source attestation or forecast activation.
"""
from datetime import date, datetime
from hashlib import sha256
import json
import math
from birzha.providers.continuous_futures import select_completed_session_volume

VERSION = "RI_CONTINUOUS_COORDINATES_V1"
COORDINATES = {"RAW_CONTRACT", "FORWARD_SPREAD", "ADDITIVE_BACK", "RATIO_BACK"}

def _stamp(value):
    x = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise ValueError("timezone required")
    return x

def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a price")
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("finite value required")
    return x

def build_continuous_series(payload):
    """Explicit calendar and exact source contracts; historical research only.

    sessions contains decision/effective_session, decision_cutoff and decision_rows
    including both prior closes on rollover. bars carry secid, timeframe,
    trade_session_date, begin/end, completed, OHLCV and source_sha256.
    """
    if not isinstance(payload, dict) or payload.get("root") != "RI":
        raise ValueError("this interface is scoped to RI")
    coordinate = payload.get("coordinate")
    if coordinate not in COORDINATES:
        raise ValueError("explicit coordinate required")
    cutoff = _stamp(payload["as_of"])
    sessions = payload["sessions"]
    bars = payload["bars"]
    if not sessions or not isinstance(sessions, list) or not isinstance(bars, list) or not bars:
        raise ValueError("nonempty source evidence required")
    if len(sessions) > 367 or len(bars) > 50000:
        raise ValueError("bounded request exceeded")
    ledger = []
    events = []
    previous = None
    seen_contracts = set()
    for session in sessions:
        day = date.fromisoformat(session["effective_session"])
        decision_day = date.fromisoformat(session["decision_session"])
        decision_cutoff = _stamp(session["decision_cutoff"])
        if decision_cutoff > cutoff or day.isoformat() > cutoff.date().isoformat():
            raise ValueError("future decision")
        if ledger and day.isoformat() <= ledger[-1]["effective_session"]:
            raise ValueError("unordered or duplicate sessions")
        if ledger and decision_day.isoformat() != ledger[-1]["effective_session"]:
            raise ValueError("ledger must cover consecutive supplied trading sessions")
        rows = session["decision_rows"]
        normalized = []
        ids = set()
        for row in rows:
            secid = row.get("SECID")
            if secid in ids:
                raise ValueError("duplicate candidate")
            ids.add(secid)
            if _stamp(row["completed_at"]) > decision_cutoff:
                raise ValueError("forming decision candle")
            if _stamp(row["available_at"]) > decision_cutoff:
                raise ValueError("late decision evidence")
            digest = row.get("source_sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("source hash required")
            normalized.append(row)
        selected = select_completed_session_volume("RI", decision_session=decision_day,
                    effective_session=day, decision_rows=normalized)
        entry = dict(effective_session=day.isoformat(), decision_session=decision_day.isoformat(),
                     decision_cutoff=decision_cutoff.isoformat(), secid=selected.secid,
                     source_hashes=sorted(row["source_sha256"] for row in rows))
        if previous and previous != selected.secid:
            if selected.secid in seen_contracts:
                raise ValueError("contract re-entry requires independent review")
            overlap = {row["SECID"]: row for row in rows}
            if previous not in overlap:
                raise ValueError("missing old contract overlap")
            old = _number(overlap[previous]["CLOSE"])
            new = _number(overlap[selected.secid]["CLOSE"])
            if old <= 0 or new <= 0:
                raise ValueError("positive rollover closes required")
            events.append(dict(old_secid=previous, new_secid=selected.secid,
                               decision_session=decision_day.isoformat(),
                               effective_session=day.isoformat(),
                               basis=new-old, ratio=new/old,
                               source_hashes=entry["source_hashes"]))
        ledger.append(entry)
        seen_contracts.add(selected.secid)
        previous = selected.secid
    cutoffs = {x["effective_session"]: _stamp(x["decision_cutoff"]) for x in ledger}
    mapping = {x["effective_session"]: x["secid"] for x in ledger}
    seen = set()
    output = []
    coverage = {}
    for bar in bars:
        day = bar["trade_session_date"]
        if day not in mapping:
            raise ValueError("bar outside ledger")
        secid = bar["secid"]
        tf = bar["timeframe"]
        if tf not in {"D1", "H1", "M15"}:
            raise ValueError("unsupported timeframe")
        begin, end = _stamp(bar["begin"]), _stamp(bar["end"])
        if cutoffs[day] > begin:
            raise ValueError("decision cutoff after target bar started")
        if begin >= end or end > cutoff or bar.get("completed") is not True:
            raise ValueError("forming or invalid bar")
        digest = bar.get("source_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("bar source hash required")
        key = (secid, tf, begin)
        if key in seen:
            raise ValueError("duplicate source bar")
        seen.add(key)
        if secid != mapping[day]:
            continue
        prices = {k:_number(bar[k]) for k in ("open","high","low","close")}
        if prices["low"] > min(prices["open"],prices["close"]) or prices["high"] < max(prices["open"],prices["close"]) or prices["low"] > prices["high"]:
            raise ValueError("invalid OHLC")
        if bar.get("volume") is not None and _number(bar["volume"]) < 0:
            raise ValueError("negative volume")
        shift, scale = 0.0, 1.0
        for event in events:
            if coordinate == "FORWARD_SPREAD" and event["effective_session"] <= day:
                shift -= event["basis"]
            elif event["effective_session"] > day:
                if coordinate == "ADDITIVE_BACK":
                    shift += event["basis"]
                elif coordinate == "RATIO_BACK":
                    scale *= event["ratio"]
        output.append(dict(bar, raw_ohlc=prices,
                           **{k:v*scale+shift for k,v in prices.items()},
                           coordinate=coordinate))
        coverage.setdefault(day,set()).add(tf)
    # This endpoint requests aligned D1/H1/M15 evidence, not three independently selected contracts.
    if any(coverage.get(day) != {"D1","H1","M15"} for day in mapping):
        raise ValueError("missing selected-contract timeframe/session")
    output.sort(key=lambda x:(x["timeframe"],_stamp(x["begin"])))
    canonical = json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    return dict(version=VERSION, root="RI", coordinate=coordinate, as_of=cutoff.isoformat(),
                ledger=ledger, roll_events=events, bars=output,
                input_sha256=sha256(canonical).hexdigest(),
                source_availability="CALLER_ASSERTED_RECONSTRUCTED_NOT_ATTESTED",
                calendar_status="CALLER_SUPPLIED_NOT_ATTESTED",
                forecast_allowed=False, raw_modified=False)
