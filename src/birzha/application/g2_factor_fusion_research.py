"""Research-only combination of *precomputed* Protocol 08 factor evidence.

No providers, MCP registration, ForecastJournal, OutcomeJournal, production
decision, model fitting or price-bar access. No statistical edge is claimed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re

VERSION = "G2_MULTIFACTOR_COMBINATION_RESEARCH_V1"
MODES = frozenset({"RECONSTRUCTED_RESEARCH_ONLY", "PROSPECTIVE_RECEIPT_RESEARCH"})
STATUSES = frozenset({"AVAILABLE", "UNAVAILABLE", "INSUFFICIENT_HISTORY", "NOT_APPLICABLE", "DEGRADED", "PROXY"})
FAMILIES = frozenset({"market_state", "control", "location", "pressure", "efficiency", "effort_result", "route", "volatility"})
# Shared factor families count once. This is a provisional grouping, NOT proof
# of independent information; Control itself may depend on price and flow.
VOTE_GROUPS = {"market_state": "PRICE", "control": "REACTION", "pressure": "FLOW", "effort_result": "FLOW"}
CONTEXT_FAMILIES = frozenset({"location", "efficiency", "volatility"})
STRATEGIES = ("GROUP_MAJORITY", "GROUP_MEAN", "STATE_CONTROL_ROUTE_CONSENSUS")


@dataclass(frozen=True, slots=True)
class FactorEvidence:
    family: str
    status: str
    signed_strength: float | None
    event_end: str | None = None
    observed_at: str | None = None
    source_sha256: str | None = None

    # Signed strength is a preregistered directional research estimate
    # in [-1,+1], NOT probability, Control score or approved Alignment.
    # An unsigned ER or volatility value must not become a directional vote.


def _aware_utc(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an offset-aware ISO string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("naive timestamp is forbidden")
    return result.astimezone(timezone.utc)


def _validated(evidence: tuple[FactorEvidence, ...], *, as_of: datetime, mode: str) -> None:
    if mode not in MODES:
        raise ValueError("unknown research evidence mode")
    if not evidence:
        raise ValueError("evidence cannot be empty")
    seen: set[str] = set()
    for item in evidence:
        if item.family not in FAMILIES or item.family in seen:
            raise ValueError("unsupported or repeated factor family")
        seen.add(item.family)
        if item.status not in STATUSES:
            raise ValueError("unsupported factor status")
        signed = item.signed_strength
        if signed is not None and (
            type(signed) not in (int, float) or not math.isfinite(signed) or not -1 <= signed <= 1
        ):
            raise ValueError("signed strength must be finite in [-1,1]")
        if item.family in CONTEXT_FAMILIES and signed is not None:
            raise ValueError("unsigned context cannot cast a directional vote")
        if item.status != "AVAILABLE" and signed is not None:
            raise ValueError("unavailable / proxy evidence cannot vote")
        if item.source_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", item.source_sha256):
            raise ValueError("invalid source SHA256")
        end = _aware_utc(item.event_end) if item.event_end is not None else None
        receipt = _aware_utc(item.observed_at) if item.observed_at is not None else None
        if end is not None and end > as_of:
            raise ValueError("future event in factor input")
        if receipt is not None and receipt > as_of:
            raise ValueError("post-decision observation in factor input")
        if end is not None and receipt is not None and receipt < end:
            raise ValueError("received before the completed event")
        if mode == "PROSPECTIVE_RECEIPT_RESEARCH" and item.status == "AVAILABLE":
            if end is None or receipt is None or item.source_sha256 is None:
                raise ValueError("prospective evidence needs event, receipt and original SHA256")


def _family_strength(evidence: tuple[FactorEvidence, ...], family: str) -> float | None:
    for item in evidence:
        if item.family == family and item.status == "AVAILABLE":
            return item.signed_strength
    return None


def _grouped(evidence: tuple[FactorEvidence, ...]) -> dict[str, float]:
    by_group: dict[str, list[float]] = {}
    for item in evidence:
        group = VOTE_GROUPS.get(item.family)
        if group and item.status == "AVAILABLE" and item.signed_strength is not None:
            by_group.setdefault(group, []).append(float(item.signed_strength))
    return {key: round(sum(values) / len(values), 8) for key, values in sorted(by_group.items())}


def _majority(groups: dict[str, float]) -> tuple[str, str]:
    if len(groups) < 2:
        return "ABSTAIN", "FEWER_THAN_TWO_VOTING_GROUPS"
    positive = sum(v >= 0.25 for v in groups.values())
    negative = sum(v <= -0.25 for v in groups.values())
    n = positive + negative
    if n < 2 or max(positive, negative) < 2:
        return "ABSTAIN", "INSUFFICIENT_DIRECTIONAL_AGREEMENT"
    if max(positive, negative) / n < 2 / 3:
        return "ABSTAIN", "NO_TWO_THIRDS_GROUP_MAJORITY"
    return ("UP" if positive > negative else "DOWN"), "PROVISIONAL_GROUP_MAJORITY"


def _mean(groups: dict[str, float]) -> tuple[str, str]:
    if len(groups) < 2:
        return "ABSTAIN", "FEWER_THAN_TWO_VOTING_GROUPS"
    mean = sum(groups.values()) / len(groups)
    supporters = sum((v >= 0.25 if mean > 0 else v <= -0.25) for v in groups.values())
    opposite = any((v <= -0.65 if mean > 0 else v >= 0.65) for v in groups.values())
    if abs(mean) < 0.45 or supporters < 2 or opposite:
        return "ABSTAIN", "WEAK_OR_CONFLICTING_GROUP_EVIDENCE"
    return ("UP" if mean > 0 else "DOWN"), "PROVISIONAL_GROUP_MEAN"


def _consensus(evidence: tuple[FactorEvidence, ...]) -> tuple[str, str]:
    # Route is derived context, never a separate independent vote.
    values = [_family_strength(evidence, family) for family in ("market_state", "control", "route")]
    if any(v is None or abs(v) < 0.4 for v in values):
        return "ABSTAIN", "STATE_CONTROL_ROUTE_UNAVAILABLE_OR_WEAK"
    sign = 1 if values[0] > 0 else -1
    if any(v * sign < 0.4 for v in values):
        return "ABSTAIN", "STATE_CONTROL_ROUTE_DISAGREE"
    pressure = _family_strength(evidence, "pressure")
    if pressure is not None and pressure * sign <= -0.65:
        return "ABSTAIN", "STRONG_OPPOSING_PRESSURE"
    return ("UP" if sign > 0 else "DOWN"), "PROVISIONAL_CONSENSUS"


def compare_factor_combinations(
    *,
    as_of: str,
    evidence: tuple[FactorEvidence, ...],
    mode: str = "RECONSTRUCTED_RESEARCH_ONLY",
) -> dict[str, object]:
    """Compare three frozen decision rules without writing or issuing forecasts.

    Inputs must be compiled from separately versioned, causally checked feature
    adapters. Output candidate directions are NOT authorized trading signals.
    """
    t0 = _aware_utc(as_of)
    if not isinstance(evidence, tuple):
        raise ValueError("immutable tuple of factor evidence required")
    _validated(evidence, as_of=t0, mode=mode)
    sorted_evidence = tuple(sorted(evidence, key=lambda x: x.family))
    payload = {
        "version": VERSION,
        "as_of_utc": t0.isoformat(),
        "mode": mode,
        "factors": [asdict(x) for x in sorted_evidence],
    }
    identity = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    groups = _grouped(sorted_evidence)
    candidates = {
        "GROUP_MAJORITY": _majority(groups),
        "GROUP_MEAN": _mean(groups),
        "STATE_CONTROL_ROUTE_CONSENSUS": _consensus(sorted_evidence),
    }
    # A volatile/unsafe regime is a veto, not a short/long vote.
    volatility = next((e for e in sorted_evidence if e.family == "volatility"), None)
    risk_veto = volatility is not None and volatility.status == "DEGRADED"
    return {
        "version": VERSION,
        "input_sha256": identity,
        "as_of_utc": t0.isoformat(),
        "mode": mode,
        "source_attestation": "NOT_INDEPENDENTLY_VERIFIED",
        "group_votes": groups,
        "counted_vote_groups": len(groups),
        "factor_statuses": {e.family: e.status for e in sorted_evidence},
        "models": {
            name: {
                "candidate_direction": ("ABSTAIN" if risk_veto else direction),
                "reason": ("VOLATILITY_RISK_VETO" if risk_veto else reason),
                "operational_decision": "NOT_AUTHORIZED",
                "probability": None,
                "statistical_edge": "UNTESTED",
            }
            for name, (direction, reason) in candidates.items()
        },
        "limitations": [
            "Research-only: never call forecast.create, journal, market fetch or order API.",
            "Groups limit duplicate votes but do not establish statistical independence.",
            "Signed factor estimates must have frozen causal producer contracts.",
            "Candidate thresholds are preregistered engineering hypotheses, not fitted or accepted.",
        ],
    }
