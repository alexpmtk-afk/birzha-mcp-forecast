"""Factual price-level origins; no inferred strength or trading targets."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math

LEVEL_EVIDENCE_VERSION = "PRICE_LEVEL_EVIDENCE_V1"
EXACT_PROFILE_METHOD = "PUBLIC_TRADES_PRICE_QUANTITY_V1"
PROXY_PROFILE_METHODS = frozenset({"CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1", "CANDLE_VOLUME_PROXY_V1"})


def evidence_time(value: str) -> datetime:
    # MOEX candle timestamps without an offset follow the snapshot convention.
    if not isinstance(value, str) or "T" not in value and " " not in value:
        raise ValueError("timestamp requires a time of day")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone(timedelta(hours=3)))


@dataclass(frozen=True, slots=True)
class PriceLevelEvidence:
    name: str
    price: float | None
    method: str
    timeframe: str | None
    source_end: str | None
    status: str
    reason: str | None
    snapshot_id: str
    secid: str
    t0: str
    strength: str = "UNKNOWN"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_level_evidence(data: dict[str, object]) -> None:
    levels = data.get("level_evidence")
    if data.get("level_evidence_version") != LEVEL_EVIDENCE_VERSION or not isinstance(levels, list):
        raise ValueError("versioned level evidence required")
    expected = [f"{tf}_{role}" for tf in ("D1", "H1", "M15") for role in ("RANGE_LOW_20", "RANGE_HIGH_20")] + ["PROFILE_VAL", "PROFILE_POC", "PROFILE_VAH"]
    if [item.get("name") for item in levels if isinstance(item, dict)] != expected:
        raise ValueError("level origins must be complete and uniquely ordered")
    profile_items = levels[-3:]
    if any(tuple(item.get(k) for k in ("method","timeframe","source_end","status","reason")) != tuple(profile_items[0].get(k) for k in ("method","timeframe","source_end","status","reason")) for item in profile_items):
        raise ValueError("profile boundaries require the same source and admission")
    admitted = []
    for item in levels:
        if item.get("snapshot_id") != data.get("snapshot_id") or item.get("secid") != data.get("secid") or item.get("t0") != data.get("created_at_t0") or item.get("strength") != "UNKNOWN":
            raise ValueError("level evidence contradicts snapshot identity or strength")
        status = item.get("status")
        price = item.get("price")
        if status not in {"AVAILABLE", "AVAILABLE_APPROXIMATE", "UNAVAILABLE", "INSUFFICIENT_HISTORY"}:
            raise ValueError("unknown level availability")
        if status in {"AVAILABLE", "AVAILABLE_APPROXIMATE"}:
            if type(price) not in (int, float) or not math.isfinite(price):
                raise ValueError("admitted level needs a finite price")
            if evidence_time(item.get("source_end")) > evidence_time(item["t0"]):
                raise ValueError("level source lies after T0")
            if item["name"].startswith("PROFILE_"):
                if status == "AVAILABLE" and item.get("method") != EXACT_PROFILE_METHOD or status == "AVAILABLE_APPROXIMATE" and item.get("method") not in PROXY_PROFILE_METHODS:
                    raise ValueError("profile precision contradicts origin")
                if item.get("timeframe") != (None if status == "AVAILABLE" else "H1"):
                    raise ValueError("profile timeframe contradicts origin")
            elif status != "AVAILABLE" or item.get("method") != "COMPLETED_HLC_RANGE_20_V1" or item.get("timeframe") != item["name"].split("_")[0]:
                raise ValueError("range level origin is invalid")
            admitted.append(price)
        elif price is not None or not isinstance(item.get("reason"), str) or not item["reason"]:
            raise ValueError("excluded levels require null price and a reason")
    if data.get("key_levels") != sorted(set(admitted)):
        raise ValueError("key levels must contain exactly admitted prices")
    for low, high in ((0,1),(2,3),(4,5)):
        if levels[low]["price"] is not None and levels[high]["price"] is not None and levels[low]["price"] > levels[high]["price"]:
            raise ValueError("range bounds are reversed")
    val, poc, vah = [item["price"] for item in levels[-3:]]
    if all(v is not None for v in (val,poc,vah)) and not val <= poc <= vah:
        raise ValueError("profile bounds are reversed")
