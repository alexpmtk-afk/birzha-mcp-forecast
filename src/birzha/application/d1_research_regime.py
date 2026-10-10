"""Uncalibrated D1 descriptive regimes; never a forecast or probability."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from math import isfinite

from birzha.application.d1_research_features import (
    D1_RESEARCH_ATR_METHOD, D1_RESEARCH_ELIGIBILITY,
    D1_RESEARCH_FEATURE_VERSION, D1ResearchFeatureSet,
)

REGIME_VERSION = "G2_D1_DESCRIPTIVE_REGIME_V1"
_REQUIRED = ("atr14_sma_tr", "d20_atr", "er20", "w20_atr")


def _finite_number(value: object) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and isfinite(value))


@dataclass(frozen=True, slots=True)
class RegimeParameters:
    """Explicit research hypotheses. No defaults and no calibrated preset."""
    hypothesis_id: str
    trend_min_abs_d20: float
    trend_min_er20: float
    balance_max_abs_d20: float
    balance_max_er20: float
    balance_max_w20: float

    def __post_init__(self) -> None:
        if not isinstance(self.hypothesis_id, str) or not self.hypothesis_id.strip():
            raise ValueError("nonempty hypothesis_id required")
        numbers = (self.trend_min_abs_d20, self.trend_min_er20,
                   self.balance_max_abs_d20, self.balance_max_er20,
                   self.balance_max_w20)
        if not all(_finite_number(x) for x in numbers):
            raise ValueError("finite non-boolean numerical thresholds required")
        if not 0 <= self.balance_max_abs_d20 < self.trend_min_abs_d20:
            raise ValueError("displacement thresholds must leave a positive gap")
        if not 0 <= self.balance_max_er20 < self.trend_min_er20 <= 1:
            raise ValueError("efficiency thresholds must leave a gap within [0, 1]")
        if self.balance_max_w20 <= 0:
            raise ValueError("positive balance width required")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        # Normalize 1 and 1.0 identically; version identifies rule semantics.
        payload = {key: (value if key == "hypothesis_id" else float(value))
                   for key, value in self.to_dict().items()}
        payload["regime_version"] = REGIME_VERSION
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RegimeResult:
    secid: str
    session: str
    state: str
    direction: str | None
    reasons: tuple[str, ...]
    diagnostics: tuple[tuple[str, float], ...]
    rules: tuple[tuple[str, bool], ...]
    parameters: RegimeParameters
    evidence_origin: str
    evidence_key: str
    feature_version: str
    atr_method: str
    version: str = REGIME_VERSION
    scope: str = "RECONSTRUCTED_RESEARCH_ONLY"
    parameter_status: str = "UNCALIBRATED_HYPOTHESIS"
    strict_historical_as_known_at_t0: bool = False

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["diagnostics"] = dict(self.diagnostics)
        result["rules"] = dict(self.rules)
        result["reasons"] = list(self.reasons)
        result["parameter_fingerprint"] = self.parameters.fingerprint
        return result


def classify_d1_research_regime(
    feature_set: D1ResearchFeatureSet, *, parameters: RegimeParameters,
) -> RegimeResult:
    """Describe the preceding window only; require independently admitted inputs.

    This validates the feature contract, not its calendar/evidence authenticity.
    UNKNOWN means either unusable input or a window outside both hypotheses.
    """
    issues: list[str] = []
    if feature_set.version != D1_RESEARCH_FEATURE_VERSION:
        issues.append("FEATURE_VERSION_MISMATCH")
    if feature_set.atr_method != D1_RESEARCH_ATR_METHOD:
        issues.append("ATR_METHOD_MISMATCH")
    if feature_set.eligibility != D1_RESEARCH_ELIGIBILITY:
        issues.append("UNSUPPORTED_EVIDENCE_SCOPE")
    if feature_set.strict_historical_as_known_at_t0 is not False:
        issues.append("UNSUPPORTED_STRICT_CAUSAL_CLAIM")
    if (not feature_set.secid or not feature_set.evidence_key
            or feature_set.evidence_origin not in (
                "RECONSTRUCTED_MOEX", "CAPTURED_AT_SYNC",
                "ARCHIVED_ACTIVE_ROOT_CALENDAR")):
        issues.append("MISSING_OR_UNSUPPORTED_SOURCE_IDENTITY")
    try:
        date.fromisoformat(feature_set.session)
    except (TypeError, ValueError):
        issues.append("INVALID_SESSION")
    if (not isinstance(feature_set.observed_bars, int)
            or isinstance(feature_set.observed_bars, bool)
            or feature_set.observed_bars < 21):
        issues.append("INSUFFICIENT_EXACT_SESSION_HISTORY")
    names = [name for name, _ in feature_set.features]
    if len(names) != len(set(names)):
        issues.append("DUPLICATE_FEATURE_NAMES")
    features = dict(feature_set.features)
    values: dict[str, float] = {}
    for name in _REQUIRED:
        item = features.get(name)
        if item is None:
            issues.append(f"MISSING_FEATURE:{name}")
        elif item.status != "AVAILABLE":
            issues.append(f"UNAVAILABLE_FEATURE:{name}:{item.status}")
        elif not _finite_number(item.value):
            issues.append(f"INVALID_FEATURE_VALUE:{name}")
        else:
            values[name] = float(item.value)
    if "atr14_sma_tr" in values and values["atr14_sma_tr"] <= 0:
        issues.append("NONPOSITIVE_ATR")
    if "er20" in values and not 0 <= values["er20"] <= 1:
        issues.append("ER20_OUT_OF_RANGE")
    if "w20_atr" in values and values["w20_atr"] < 0:
        issues.append("NEGATIVE_WIDTH")

    rules: dict[str, bool] = {}
    state, direction = "UNKNOWN", None
    if not issues:
        magnitude = abs(values["d20_atr"])
        rules = {
            "trend_displacement": magnitude >= parameters.trend_min_abs_d20,
            "trend_efficiency": values["er20"] >= parameters.trend_min_er20,
            "balance_displacement": magnitude <= parameters.balance_max_abs_d20,
            "balance_efficiency": values["er20"] <= parameters.balance_max_er20,
            "balance_width": values["w20_atr"] <= parameters.balance_max_w20,
        }
        if rules["trend_displacement"] and rules["trend_efficiency"]:
            state = "TREND"
            direction = "UP" if values["d20_atr"] > 0 else "DOWN"
            issues.append("TREND_HYPOTHESIS_MATCH")
        elif all(rules[key] for key in (
                "balance_displacement", "balance_efficiency", "balance_width")):
            state = "BALANCE"
            issues.append("BALANCE_HYPOTHESIS_MATCH")
        else:
            issues.append("OUTSIDE_DECLARED_REGIME_HYPOTHESES")

    return RegimeResult(
        secid=feature_set.secid, session=feature_set.session,
        state=state, direction=direction, reasons=tuple(issues),
        diagnostics=tuple(sorted(values.items())),
        rules=tuple(sorted(rules.items())), parameters=parameters,
        evidence_origin=feature_set.evidence_origin,
        evidence_key=feature_set.evidence_key,
        feature_version=feature_set.version, atr_method=feature_set.atr_method,
    )
