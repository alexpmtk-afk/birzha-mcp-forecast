"""Fail-closed schema preflight for the proposed M1 v2.2 parameter manifest.

This does not choose thresholds, admit historical data, or enable forecasts.
Canonical source: Project Map / «Модель 1 — инструкция», revision 2.2.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

MODEL_ID = "M1_GPT_STRUCTURE_CONTROL"
SPECIFICATION_VERSION = "2.2"
INSTRUMENTS = frozenset({"SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"})

# Deliberately no numerical defaults: each parameter must be explicit and reviewed.
REQUIRED_FIELDS = (
    "model_id", "model_version", "specification_version", "instrument_scope",
    "fit_start", "fit_end", "input_hash", "calendar_version",
    "warmup", "trailing_window", "swing_w", "consolidation_windows",
    "ER_balance", "range_balance_atr", "ER_trend", "displacement_min_atr",
    "breakout_buffer_atr", "confirm_bars", "hold_bars", "retest_zone_atr",
    "reclaim_buffer_atr", "chain_max_age", "RVOL_reference", "RVOL_min_count",
    "event_quantiles", "level_merge_atr", "level_strength_rules",
    "expected_move_method", "target_band_quantile", "min_reward_risk",
    "horizon_activation_rules", "horizon_expiry_rules",
    "units", "boundary_rules", "tie_break_rules",
)
POSITIVE_INTS = frozenset({
    "warmup", "trailing_window", "swing_w", "confirm_bars", "hold_bars",
    "chain_max_age", "RVOL_min_count",
})
NONNEGATIVE_NUMBERS = frozenset({
    "range_balance_atr", "displacement_min_atr", "breakout_buffer_atr",
    "retest_zone_atr", "reclaim_buffer_atr", "level_merge_atr",
})
UNIT_INTERVAL = frozenset({"ER_balance", "ER_trend", "target_band_quantile"})
NONEMPTY_OBJECTS = frozenset({
    "RVOL_reference", "event_quantiles", "level_strength_rules",
    "expected_move_method", "horizon_activation_rules", "horizon_expiry_rules",
    "units", "boundary_rules", "tie_break_rules",
})


def audit_m1_parameter_manifest(manifest: Mapping[str, object] | None) -> dict[str, object]:
    """Return schema issues, never a deployment/forecast authorization.

    CONFIG_REQUIRED means essential fields are missing or malformed.
    REVIEW_REQUIRED means only the *outer schema* is present: parameter
    provenance, exact algorithms, calibration and owner approval are unverified.
    """
    if not isinstance(manifest, Mapping):
        return {"model_id": MODEL_ID, "specification_version": SPECIFICATION_VERSION,
                "status": "CONFIG_REQUIRED", "issues": ["manifest: missing mapping"],
                "forecast_allowed": False}

    issues: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in manifest or manifest[field] is None:
            issues.append(f"{field}: missing")
            continue
        value = manifest[field]
        if isinstance(value, str) and (not value.strip() or value.strip().upper() in {"TBD", "TODO", "UNKNOWN", "UNAVAILABLE"}):
            issues.append(f"{field}: unresolved")
        elif field in POSITIVE_INTS and (type(value) is not int or value < 1):
            issues.append(f"{field}: expected positive integer")
        elif field in NONNEGATIVE_NUMBERS and not _finite_number(value, minimum=0):
            issues.append(f"{field}: expected nonnegative finite number")
        elif field in UNIT_INTERVAL and (not _finite_number(value, minimum=0) or value > 1):
            issues.append(f"{field}: expected finite number in [0, 1]")
        elif field in NONEMPTY_OBJECTS and (not isinstance(value, Mapping) or not value):
            issues.append(f"{field}: expected nonempty mapping")

    if manifest.get("model_id") is not None and manifest["model_id"] != MODEL_ID:
        issues.append("model_id: not M1")
    if manifest.get("specification_version") is not None and manifest["specification_version"] != SPECIFICATION_VERSION:
        issues.append("specification_version: not 2.2")

    scope = manifest.get("instrument_scope")
    if scope is not None and (
        not isinstance(scope, (list, tuple))
        or not scope or any(not isinstance(s, str) for s in scope)
        or set(scope) - INSTRUMENTS or len(scope) != len(set(scope))
    ):
        issues.append("instrument_scope: expected distinct canonical instruments")

    for field in ("model_version", "input_hash", "calendar_version"):
        if field in manifest and (not isinstance(manifest[field], str) or not manifest[field].strip()):
            issues.append(f"{field}: expected nonempty identifier")

    for field in ("fit_start", "fit_end"):
        if field in manifest:
            try:
                if type(manifest[field]) is not str:
                    raise ValueError("date must be ISO string")
                date.fromisoformat(manifest[field])
            except ValueError:
                issues.append(f"{field}: expected ISO date")
    if type(manifest.get("fit_start")) is str and type(manifest.get("fit_end")) is str:
        try:
            if date.fromisoformat(manifest["fit_start"]) >= date.fromisoformat(manifest["fit_end"]):
                issues.append("fit window: start must precede end")
        except ValueError:
            pass

    windows = manifest.get("consolidation_windows")
    if windows is not None and (
        not isinstance(windows, (list, tuple)) or not windows
        or any(type(w) is not int or w < 1 for w in windows)
        or len(windows) != len(set(windows))
    ):
        issues.append("consolidation_windows: expected distinct positive integers")
    if "min_reward_risk" in manifest and not _finite_number(manifest["min_reward_risk"], minimum=0, strict=True):
        issues.append("min_reward_risk: expected positive finite number")

    return {
        "model_id": MODEL_ID,
        "specification_version": SPECIFICATION_VERSION,
        "status": "CONFIG_REQUIRED" if issues else "REVIEW_REQUIRED",
        "issues": issues,
        "forecast_allowed": False,
    }


def _finite_number(value: object, *, minimum: float, strict: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
        return math.isfinite(number) and (number > minimum if strict else number >= minimum)
    except (TypeError, ValueError, OverflowError):
        return False
