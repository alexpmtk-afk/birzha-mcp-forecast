"""Protocol 08 independent, non-executing semantic admission review.

The current Forecast v0.4 baseline is a causal *directional heuristic*.
Its BUYERS/SELLERS and TREND strings are derived from the same combined
directional score and DO NOT establish Control holding, obstacle-aware Route,
or a fully scored Scenario. This module NEVER mutates a Forecast, signs a
release decision or sends an order.

Source: user's '08 - Прогнозирование движения цены', Universal Market
Forecasting Protocol v1.0, version available on Google Drive 2026-10-11.
"""
from __future__ import annotations

from datetime import datetime
import math
from typing import Any

from birzha.domain.forecast import ForecastRecord

SCHEMA = "G2_PROTOCOL08_SEMANTIC_ACCEPTANCE_V1"
DECISION_SCHEMA = "G2_PROTOCOL08_MANDATORY_CONDITION_REVIEW_V1"
REQUIRED_HORIZONS = (5, 10, 20)
MARKET_STATES = frozenset({
    "TREND_UP", "TREND_DOWN", "BALANCE",
    "ACCUMULATION", "DISTRIBUTION", "EVENT",
})
LOCATIONS = frozenset({"ABOVE_VAH", "BELOW_VAL", "AT_POC", "INSIDE_VALUE"})
PRESSURES = frozenset({"BUYERS_FAVORED", "SELLERS_FAVORED", "NEUTRAL"})
CONTROLS = frozenset({"BUYERS", "SELLERS", "BALANCE"})
ROUTE_CONFIDENCES = frozenset({"HIGH", "MEDIUM", "LOW"})


def _valid_utc(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.tzinfo is not None and dt.utcoffset() is not None
    except ValueError:
        return False


def audit_protocol08_forecast(
    forecast: ForecastRecord, *, market_state_vector: dict | None = None,
) -> dict[str, Any]:
    """Read-only evidence inventory, never retroactive release-grade approval.

    A baseline label and a well-shaped record do not authenticate receipts
    or prove that MarketState/Control/Route/Scenario engines have run.
    """
    if not isinstance(forecast, ForecastRecord):
        raise TypeError("immutable ForecastRecord required")
    time_ok = _valid_utc(forecast.created_at_t0)
    horizons = tuple(h.sessions for h in forecast.horizons)
    if len(horizons) != len(set(horizons)):
        raise ValueError("duplicate Forecast horizon")
    snapshot_bound = bool(forecast.snapshot_id and forecast.snapshot_contract_version)
    vector_matched = False
    vector_classified = False
    if market_state_vector is not None:
        if not isinstance(market_state_vector, dict):
            raise ValueError("market state evidence must be a mapping")
        vector_matched = (
            market_state_vector.get("snapshot_id") == forecast.snapshot_id
            and market_state_vector.get("symbol") == forecast.symbol
            and market_state_vector.get("secid") == forecast.secid
            and market_state_vector.get("as_of") == forecast.created_at_t0
        )
        if not vector_matched:
            raise ValueError("Market State vector does not match frozen Forecast T0")
        vector_classified = (
            market_state_vector.get("classification_status") == "CLASSIFIED"
            and forecast.market_state in MARKET_STATES
        )

    names = [
        ("snapshot_binding", "STRUCTURE_PRESENT" if snapshot_bound and time_ok else "MISSING",
         "snapshot reference and timezone-aware T0 are a contract shape, not independent first receipt"),
        ("future_session_horizons", "STRUCTURE_PRESENT" if tuple(sorted(horizons)) == REQUIRED_HORIZONS else "MISSING",
         "5/10/20 labels are recorded; no genuine future market outcomes exist in this review"),
        ("market_state_classifier", "UNVERIFIED" if vector_classified else "NOT_IMPLEMENTED",
         "Market State v0 is an evidence vector with NOT_CLASSIFIED, not a verified six-regime engine"),
        ("location", "UNVERIFIED" if forecast.location in LOCATIONS else "MISSING",
         "Location needs exact price/POC/VAL/VAH and quality-aware causal provenance"),
        ("pressure", "UNVERIFIED" if forecast.pressure in PRESSURES else "MISSING",
         "Pressure is an initiative measure and must never be substituted for held Control"),
        ("control_holding_confidence", "NOT_IMPLEMENTED",
         "baseline BUYERS/SELLERS/BALANCE is mapped from combined directional score; no holding/reaction/effort-to-result 0-10 confidence contract"),
        ("level_role_strength_and_history", "NOT_IMPLEMENTED",
         "price-only key_levels do not contain role, WEAK/MEDIUM/STRONG strength, tests or role-flip history"),
        ("route_obstacles_and_confidence", "NOT_IMPLEMENTED",
         "baseline TREND/BALANCE has no free-space, typed obstacles, targets or HIGH/MEDIUM/LOW route confidence"),
        ("alignment_scoring_formula", "SPECIFICATION_TBD",
         "Protocol 08 explicitly leaves the reproducible 0-15 Alignment score allocation unapproved"),
        ("scenario_mandatory_conditions", "NOT_IMPLEMENTED",
         "no approved LONG/SHORT evidence of Control confidence >=6, Route confidence and Alignment; narrative scenarios are not execution admission"),
        ("forecast_execution_separation", "INFORMATIONAL_ONLY",
         "no validated independent execution-entry, structure-based stop, targets and reversal lifecycle; never issue live orders"),
        ("future_outcome_and_learning", "PENDING_FUTURE_EVIDENCE",
         "UP_FIRST/DOWN_FIRST/NEITHER, excursions and >=100 prospective-forecast calibration are not certified by a frozen baseline"),
        ("independent_source_and_time_attestation", "NOT_VERIFIED",
         "ForecastRecord alone cannot attest original provider receipts, exact SECID calendar and genuine approved TSA"),
    ]
    checks = {name: {"status": status, "evidence_boundary": description}
              for name, status, description in names}
    unsupported = [name for name, item in checks.items()
                   if item["status"] != "STRUCTURE_PRESENT"]
    return {
        "schema": SCHEMA,
        "forecast_id": forecast.forecast_id,
        "snapshot_id": forecast.snapshot_id,
        "market_state_vector_matched": vector_matched,
        "market_state_vector_classified": vector_classified,
        "forecast_engine_version": forecast.engine_version,
        "forecast_record_version": forecast.record_version,
        "validation_status": forecast.validation_status,
        "fields": checks,
        "not_satisfied_for_full_protocol08": unsupported,
        "full_protocol08_accepted": False,
        "trade_executable": False,
        "production_authorized": False,
        "forecast_payload_mutated": False,
    }


def evaluate_mandatory_conditions(
    *, side: str, control: str, control_confidence: int | float | None,
    route_confidence: str | None,
    alignment_score: int | float | None,
    scoring_formula_approved: bool = False,
    market_state: str | None = None,
) -> dict[str, Any]:
    """Structural Engine-3 threshold evaluation; NEVER an order or release.

    The official reproducible Alignment formula is TBD. Even a caller's
    asserted scoring_formula_approved cannot independently certify it.
    """
    if side not in {"LONG", "SHORT"}:
        raise ValueError("requested scenario side must be LONG or SHORT")
    if control not in CONTROLS:
        raise ValueError("invalid Control enum")
    if market_state is not None and market_state not in MARKET_STATES:
        raise ValueError("invalid Market State")
    if route_confidence is not None and route_confidence not in ROUTE_CONFIDENCES:
        raise ValueError("invalid Route confidence")

    def number(value, maximum):
        return (type(value) in (int, float) and math.isfinite(value)
                and 0 <= value <= maximum)

    if control_confidence is not None and not number(control_confidence, 10):
        raise ValueError("Control confidence must be finite 0..10")
    if alignment_score is not None and not number(alignment_score, 15):
        raise ValueError("Alignment must be finite 0..15 and not probability")
    if type(scoring_formula_approved) is not bool:
        raise ValueError("scoring approval is boolean, never inferred")

    reasons = []
    expected = "BUYERS" if side == "LONG" else "SELLERS"
    if control != expected:
        reasons.append("CONTROL_SIDE_MISMATCH")
    if control_confidence is None:
        reasons.append("CONTROL_CONFIDENCE_MISSING")
    elif control_confidence < 6:
        reasons.append("CONTROL_CONFIDENCE_BELOW_6")
    if route_confidence is None:
        reasons.append("ROUTE_CONFIDENCE_MISSING")
    elif route_confidence == "LOW":
        reasons.append("ROUTE_CONFIDENCE_LOW")
    if market_state == "BALANCE":
        reasons.append("ROUTE_FORBIDDEN_IN_BALANCE")
    if alignment_score is None:
        reasons.append("ALIGNMENT_SCORE_MISSING")
    elif alignment_score < 7:
        reasons.append("ALIGNMENT_BELOW_7")
    elif alignment_score < 10:
        reasons.append("ALIGNMENT_7_TO_10_NEEDS_CONFIRMATION")
    if not scoring_formula_approved:
        reasons.append("ALIGNMENT_FORMULA_TBD")
    # Synthetic / caller-claimed policy approval remains conditional at best.
    if not reasons:
        decision = "CANDIDATE_CONDITIONAL_NOT_EXECUTABLE"
    else:
        decision = "NO_TRADE_OR_WITHHOLD_SCENARIO"
    return {
        "schema": DECISION_SCHEMA,
        "requested_side": side,
        "control": control,
        "control_confidence": control_confidence,
        "route_confidence": route_confidence,
        "alignment_score_out_of_15_not_probability": alignment_score,
        "status": decision,
        "blocking_reasons": reasons,
        "scoring_formula_independently_certified": False,
        "source_and_time_independently_attested": False,
        "execution_authorized": False,
        "production_authorized": False,
    }
