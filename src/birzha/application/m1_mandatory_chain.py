"""Read-only, untrusted-evidence preflight for M1 v2.2 Mandatory Chain.

This module *does not calculate* price patterns or verify submitted evidence
against bars, calendars or receipts. In particular it never emits ACTIVE,
a trade, a saved forecast, or an approval. Missing proof fails closed.
Canonical reference: Project Map, «Модель 1 — инструкция», rev 2.2,
sections 04/05, 11/13/14.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

MODEL_ID = "M1_GPT_STRUCTURE_CONTROL"
HORIZONS = (5, 10, 20)
SHARED_KEYS = (
    "data_admissible",
    "calendar_admissible",
    "manifest_configured",
    "anchor_confirmed",
    "h1_retested_control",
    "m15_same_level_retest_hold",
    "causal_times",
    "no_event",
    "contract_compatible",
)
HORIZON_KEYS = ("route_usable", "target_reachable", "invalidation_valid", "expiry_defined")


@dataclass(frozen=True, slots=True)
class Evidence:
    """Claim + independently identifiable provenance (not verified here)."""

    status: str  # TRUE, FALSE, UNAVAILABLE
    source_ref: str | None = None
    event_time: datetime | None = None
    confirmed_at: datetime | None = None
    available_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ChainContext:
    t0: datetime
    direction: str  # LONG / SHORT
    d1_state: str
    h1_side: str
    h1_strength: str
    h1_causal_status: str
    h1_level_id: str | None
    m15_level_id: str | None
    d1_secid: str | None
    h1_secid: str | None
    m15_secid: str | None
    event_flag: bool | None


def audit_m1_mandatory_chain(
    context: ChainContext,
    shared_evidence: Mapping[str, Evidence],
    horizon_evidence: Mapping[int, Mapping[str, Evidence]],
) -> dict[str, object]:
    """Inspect structure, causal timestamps and all three independent gates.

    Results are *non-executable* diagnostic states; evidence is caller-supplied.
    Even CHAIN_CONSISTENT cannot authorize a model, predict price or save data.
    """
    if not _aware(context.t0):
        raise ValueError("T0 must be timezone-aware")
    failures, unknowns = _context_checks(context)
    shared_false, shared_unknown = _check_keys(shared_evidence, SHARED_KEYS, context.t0)
    failures.extend(shared_false)
    unknowns.extend(shared_unknown)

    results: dict[str, dict[str, object]] = {}
    for horizon in HORIZONS:
        subset = horizon_evidence.get(horizon)
        h_false, h_unknown = _check_keys(subset or {}, HORIZON_KEYS, context.t0)
        errors = sorted(set(failures + h_false))
        unavailable = sorted(set(unknowns + h_unknown))
        state = "CONDITION_FALSE" if errors else "EVIDENCE_UNAVAILABLE" if unavailable else "CHAIN_CONSISTENT_UNVERIFIED"
        results[f"H{horizon}"] = {
            "state": state,
            "failed_conditions": errors,
            "unavailable_conditions": unavailable,
            "forecast_allowed": False,
        }
    extra_h = sorted(set(horizon_evidence) - set(HORIZONS), key=str)
    return {
        "model_id": MODEL_ID,
        "status": "DIAGNOSTIC_ONLY",
        "horizons": results,
        "unexpected_horizons": list(map(str, extra_h)),
        "evidence_attested": False,
        "forecast_allowed": False,
    }


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _check_keys(
    evidence: Mapping[str, Evidence], keys: tuple[str, ...], t0: datetime,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    unknowns: list[str] = []
    for key in keys:
        item = evidence.get(key)
        if not isinstance(item, Evidence):
            unknowns.append(f"{key}: missing evidence")
            continue
        if item.status == "FALSE":
            failures.append(f"{key}: explicitly false")
        elif item.status != "TRUE":
            unknowns.append(f"{key}: unavailable or invalid status")
        elif not item.source_ref or not item.source_ref.strip():
            unknowns.append(f"{key}: no provenance reference")
        elif not all(_aware(t) for t in (item.event_time, item.confirmed_at, item.available_at)):
            unknowns.append(f"{key}: timestamps missing or naive")
        elif not (item.event_time <= item.confirmed_at <= item.available_at <= t0):
            unknowns.append(f"{key}: timestamps not causal at T0")
    return failures, unknowns


def _context_checks(c: ChainContext) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    unknowns: list[str] = []

    if c.direction not in ("LONG", "SHORT"):
        failures.append("direction: expected LONG or SHORT")
    else:
        expected = ("TREND_UP", "BUYERS") if c.direction == "LONG" else ("TREND_DOWN", "SELLERS")
        if c.d1_state != expected[0]:
            (unknowns if c.d1_state == "UNKNOWN" else failures).append("D1 direction mismatch or unknown")
        if c.h1_side != expected[1]:
            (unknowns if c.h1_side == "UNCONFIRMED" else failures).append("H1 control side mismatch or unknown")

    if c.h1_strength != "RETESTED":
        (unknowns if c.h1_strength in ("", "UNKNOWN") else failures).append("H1 requires observed RETESTED control")
    if c.h1_causal_status != "CONFIRMED":
        (unknowns if c.h1_causal_status in ("PENDING", "UNAVAILABLE") else failures).append("H1 control is not causally confirmed")
    if not c.h1_level_id or not c.m15_level_id:
        unknowns.append("H1/M15: missing frozen level ID")
    elif c.h1_level_id != c.m15_level_id:
        failures.append("H1/M15: retest/hold level mismatch")

    if not all((c.d1_secid, c.h1_secid, c.m15_secid)):
        unknowns.append("SECID: missing exact contract")
    elif len({c.d1_secid, c.h1_secid, c.m15_secid}) != 1:
        failures.append("SECID: mixed contracts across timeframes")
    if c.event_flag is True:
        failures.append("EVENT: directional release blocked")
    elif c.event_flag is not False:
        unknowns.append("EVENT: state unavailable")
    return failures, unknowns
