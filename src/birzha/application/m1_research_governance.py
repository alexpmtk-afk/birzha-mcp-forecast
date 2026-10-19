"""M1 v2.2 research split / causal outcome availability *preflight*.

No data is loaded and no parameters are fitted, tuned, scored or approved.
Only an explicitly supplied TRAIN/DEVELOPMENT receipt manifest is inspected.
FINAL_HOLDOUT observations are not accepted by this function.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

HORIZONS = (5, 10, 20)


@dataclass(frozen=True, slots=True)
class SplitPlan:
    """Session ordinals are from a separately versioned, *trusted* calendar.

    The interval between TRAIN and DEVELOPMENT must include explicit
    purged/embargoed sessions. Neither ordinals nor calendar are invented here.
    """
    train_first: int
    train_last: int
    development_first: int
    development_last: int
    embargo_sessions: int
    candidate_grid_hash: str
    metric_id: str
    tie_break_id: str
    calendar_version: str
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class OutcomeSample:
    split: str  # TRAIN or DEVELOPMENT. FINAL_HOLDOUT forbidden.
    forecast_session: int
    horizon_sessions: int
    outcome_end_session: int
    outcome_available_session: int
    source_fingerprint: str


def audit_m1_research_split(
    plan: SplitPlan,
    samples: Iterable[OutcomeSample],
) -> dict[str, object]:
    """Flag leakage and missing research protocol, without doing research."""
    issues: list[str] = []
    ints = (plan.train_first, plan.train_last, plan.development_first,
            plan.development_last, plan.embargo_sessions)
    if any(type(v) is not int or v < 0 for v in ints):
        issues.append("plan: session ordinals and embargo must be nonnegative integers")
    else:
        if not (plan.train_first < plan.train_last < plan.development_first < plan.development_last):
            issues.append("plan: ordered non-overlapping session windows required")
        if plan.embargo_sessions < max(HORIZONS):
            issues.append("plan: embargo must cover maximal 20-session outcome")
        if plan.development_first - plan.train_last - 1 < plan.embargo_sessions:
            issues.append("plan: insufficient purged/embargoed session gap")

    for field in ("candidate_grid_hash", "metric_id", "tie_break_id", "calendar_version", "source_fingerprint"):
        value = getattr(plan, field)
        if not isinstance(value, str) or not value.strip() or value.upper() in {"TBD", "TODO", "UNKNOWN"}:
            issues.append(f"plan: {field} not frozen")
    if not _sha256(plan.candidate_grid_hash):
        issues.append("plan: candidate_grid_hash must be SHA256")
    if not _sha256(plan.source_fingerprint):
        issues.append("plan: source_fingerprint must be SHA256")

    data = tuple(samples)
    if not data:
        issues.append("samples: no TRAIN/DEVELOPMENT receipts")
    partitions: set[str] = set()
    sample_keys: set[tuple[str, int, int]] = set()
    for i, sample in enumerate(data):
        prefix = f"sample[{i}]"
        if not isinstance(sample, OutcomeSample):
            issues.append(f"{prefix}: invalid record type")
            continue
        if sample.split not in ("TRAIN", "DEVELOPMENT"):
            issues.append(f"{prefix}: forbidden/unknown split (FINAL_HOLDOUT must remain sealed)")
            continue
        partitions.add(sample.split)
        indexes = (sample.forecast_session, sample.horizon_sessions,
                   sample.outcome_end_session, sample.outcome_available_session)
        if any(type(value) is not int or value < 0 for value in indexes):
            issues.append(f"{prefix}: invalid session ordinal")
            continue
        if sample.horizon_sessions not in HORIZONS:
            issues.append(f"{prefix}: unsupported horizon")
            continue
        if sample.outcome_end_session != sample.forecast_session + sample.horizon_sessions:
            issues.append(f"{prefix}: outcome must finish after exact H following sessions")
        if sample.outcome_available_session < sample.outcome_end_session:
            issues.append(f"{prefix}: outcome cannot be available before it ends")
        if sample.source_fingerprint != plan.source_fingerprint:
            issues.append(f"{prefix}: source fingerprint mismatch")
        first, last = ((plan.train_first, plan.train_last) if sample.split == "TRAIN"
                       else (plan.development_first, plan.development_last))
        if not (first <= sample.forecast_session <= last):
            issues.append(f"{prefix}: forecast origin outside declared split")
        if sample.outcome_available_session > last:
            issues.append(f"{prefix}: look-ahead, outcome matures outside split")
        key = (sample.split, sample.forecast_session, sample.horizon_sessions)
        if key in sample_keys:
            issues.append(f"{prefix}: duplicate sample")
        sample_keys.add(key)

    if not {"TRAIN", "DEVELOPMENT"}.issubset(partitions):
        issues.append("samples: both TRAIN and DEVELOPMENT must have receipts")
    return {
        "model_id": "M1_GPT_STRUCTURE_CONTROL",
        "status": "CONFIG_REQUIRED" if issues else "RESEARCH_PREFLIGHT_ONLY",
        "issues": sorted(set(issues)),
        "samples_examined": len(data),
        "final_holdout_accessed": False,
        "parameters_fitted": False,
        "selection_authorized": False,
        "forecast_allowed": False,
    }


def _sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
