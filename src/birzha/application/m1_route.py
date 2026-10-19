"""M1 v2.2 read-only research obstacle map, never a forecast gate.

Structural zones/receipts are caller assertions, not authenticated market data.
No level fitting, synthetic zeroes, source adapter, merge, deployment or trading.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping, Sequence

MODEL_ID = "M1_GPT_STRUCTURE_CONTROL"
HORIZONS = (5, 10, 20)
STRUCTURAL_TYPES = frozenset({"SWING", "CONSOLIDATION", "DAILY_EXTREME", "WEEKLY_EXTREME"})
PROFILE_TYPES = frozenset({"POC", "HVN", "LVN"})
ROLES = frozenset({"RESISTANCE", "SUPPORT", "BOTH", "UNKNOWN"})


@dataclass(frozen=True, slots=True)
class RoutePrice:
    secid: str
    value: float
    observed_at: datetime
    available_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class RouteZone:
    level_id: str
    secid: str
    timeframe: str
    lower: float
    upper: float
    level_type: str
    role: str
    quality: str  # CONFIRMED, PROVISIONAL, UNKNOWN
    source_type: str  # PRICE_STRUCTURE, TRADES_BASED, CANDLE_VOLUME_PROXY
    event_time: datetime
    confirmed_at: datetime
    available_at: datetime
    source_ref: str


def analyze_m1_route(
    *, t0: datetime, p0: RoutePrice, direction: str,
    zones: Sequence[RouteZone], horizon_scopes: Mapping[int, float | None],
) -> dict[str, object]:
    """Report nearest forward structural zone for each explicit horizon scope.

    A scope is a caller-predeclared positive *price-distance* envelope, NOT a
    calculated expected move or a selected target. No scope means CONFIG_REQUIRED.
    A missing or unknown barrier is UNKNOWN_COVERAGE, never an infinite route.
    Tie-break: price distance, earlier confirmed_at, lexicographic level_id.
    """
    if not _aware(t0):
        raise ValueError("timezone-aware T0 required")
    if not isinstance(p0, RoutePrice) or not _id(p0.secid) or not _finite(p0.value):
        raise ValueError("finite P0 and exact SECID required")
    if not _aware(p0.observed_at) or not _aware(p0.available_at):
        raise ValueError("P0 timestamps must be timezone-aware")
    if not p0.observed_at <= p0.available_at <= t0 or not _id(p0.source_ref):
        raise ValueError("P0 must be known by T0 with source reference")
    if direction not in ("UP", "DOWN"):
        raise ValueError("route direction must be UP or DOWN")
    if not isinstance(horizon_scopes, Mapping):
        raise ValueError("explicit horizon scopes required")
    if any(type(h) is not int or h not in HORIZONS for h in horizon_scopes):
        raise ValueError("unsupported horizon; only H5/H10/H20")

    # Do not even inspect role/price/quality of objects not known at T0.
    admitted: list[RouteZone] = []
    seen: set[str] = set()
    for zone in zones:
        if not isinstance(zone, RouteZone):
            raise ValueError("RouteZone record required")
        if not _aware(zone.available_at) or not _aware(zone.confirmed_at) or not _aware(zone.event_time):
            raise ValueError("zone timestamps must be timezone-aware")
        if zone.available_at > t0 or zone.confirmed_at > t0:
            continue
        if not zone.event_time <= zone.confirmed_at <= zone.available_at:
            raise ValueError("zone has impossible causal timestamps")
        if zone.secid != p0.secid or zone.timeframe not in ("D1", "H1", "M15"):
            raise ValueError("zone contract/timeframe conflicts with P0")
        if not _id(zone.level_id) or not _id(zone.source_ref):
            raise ValueError("zone needs immutable ID and provenance")
        if zone.level_id in seen:
            raise ValueError("duplicate level ID; ambiguous version of zone")
        seen.add(zone.level_id)
        if not _finite(zone.lower) or not _finite(zone.upper) or zone.lower > zone.upper:
            raise ValueError("invalid finite price zone")
        if zone.level_type not in STRUCTURAL_TYPES | PROFILE_TYPES:
            raise ValueError("unregistered level type")
        if zone.source_type not in ("PRICE_STRUCTURE", "TRADES_BASED", "CANDLE_VOLUME_PROXY"):
            raise ValueError("unregistered level source type")
        if zone.role not in ROLES or zone.quality not in ("CONFIRMED", "PROVISIONAL", "UNKNOWN"):
            raise ValueError("invalid zone role/quality")
        admitted.append(zone)

    usable: list[tuple[float, RouteZone]] = []
    unresolved: list[tuple[float, RouteZone]] = []
    context_only: list[str] = []
    for z in admitted:
        if z.source_type != "PRICE_STRUCTURE" or z.level_type not in STRUCTURAL_TYPES:
            context_only.append(z.level_id)
            continue
        # An obstacle must not be entirely behind P0. The closest boundary,
        # rather than the zone center, is used; inside the zone => distance 0.
        if direction == "UP":
            if z.upper < p0.value:
                continue
            d = max(0.0, z.lower - p0.value)
            compatible_role = z.role in ("RESISTANCE", "BOTH")
            unresolved_role = z.role == "UNKNOWN"
        else:
            if z.lower > p0.value:
                continue
            d = max(0.0, p0.value - z.upper)
            compatible_role = z.role in ("SUPPORT", "BOTH")
            unresolved_role = z.role == "UNKNOWN"
        if z.quality == "CONFIRMED" and compatible_role:
            usable.append((d, z))
        elif z.quality != "CONFIRMED" or unresolved_role:
            unresolved.append((d, z))
        # A confirmed wrong-side support/resistance is not a blocking obstacle.

    def rank(entry: tuple[float, RouteZone]) -> tuple[float, datetime, str]:
        distance, z = entry
        return (distance, z.confirmed_at, z.level_id)

    usable.sort(key=rank)
    unresolved.sort(key=rank)
    result: dict[str, object] = {}
    for h in HORIZONS:
        scope = horizon_scopes.get(h)
        if scope is None:
            result[f"H{h}"] = {
                "status": "CONFIG_REQUIRED", "nearest_obstacle": None,
                "reason": "horizon price envelope not provided",
                "scope_price_distance": None, "obstacle_count": None,
                "forecast_allowed": False,
            }
            continue
        if not _finite(scope) or scope <= 0:
            raise ValueError(f"H{h}: positive finite price envelope required")
        bounded = [entry for entry in usable if entry[0] <= scope]
        uncertain = [entry for entry in unresolved if entry[0] <= scope]
        nearest = bounded[0] if bounded else None
        nearer_uncertainty = bool(uncertain) and (nearest is None or uncertain[0][0] <= nearest[0])
        status = ("UNRESOLVED_FORWARD_ZONE" if nearer_uncertainty else
                  "STRUCTURAL_OBSTACLE" if nearest is not None else "UNKNOWN_COVERAGE")
        obstacle = None
        if nearest is not None:
            distance, zone = nearest
            obstacle = {
                "level_id": zone.level_id,
                "distance_to_nearest_boundary": distance,
                "lower": zone.lower, "upper": zone.upper,
                "inside_zone": distance == 0,
                "confirmed_at": zone.confirmed_at,
                "available_at": zone.available_at,
                "source_ref": zone.source_ref,
                "quality": zone.quality,
            }
        result[f"H{h}"] = {
            "status": status, "nearest_obstacle": obstacle,
            "scope_price_distance": scope,
            "obstacle_count": len(bounded),
            "unresolved_level_ids": tuple(z.level_id for _, z in uncertain),
            "reason": "coverage not independently attested" if status == "UNKNOWN_COVERAGE" else None,
            "forecast_allowed": False,
        }
    return {
        "model_id": MODEL_ID, "status": "ROUTE_RESEARCH_ONLY",
        "t0": t0, "p0": asdict(p0), "direction": direction,
        "horizons": result, "profile_context_level_ids": tuple(sorted(context_only)),
        "known_level_ids": tuple(sorted(z.level_id for z in admitted)),
        "levels_attested": False, "coverage_attested": False,
        "source_receipts_attested": False, "forecast_allowed": False,
    }


def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError, TypeError):
        return False


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _id(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
