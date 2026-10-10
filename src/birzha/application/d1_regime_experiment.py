"""Executable simulation of the proposed quantile-11 procedure, in memory only.

No real experiment admission, predictive validation, or file publication.
Preparation and evaluation are separate, externally hash-anchored stages.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import json
from math import isfinite

from birzha.application.d1_research_dataset_adapter import (
    MARKETS, _decode, canonical_json, sha256, validate_d1_research_dataset,
)
from birzha.application.d1_research_features import D1_RESEARCH_FEATURE_VERSION
from birzha.application.d1_research_regime import REGIME_VERSION, RegimeParameters
from birzha.application.d1_research_runner import run_d1_descriptive_research

EXPERIMENT_VERSION = "G2_PROPOSED_QUANTILE11_SIMULATION_V1"
_FIELDS = ("trend_min_abs_d20", "trend_min_er20", "balance_max_abs_d20",
           "balance_max_er20", "balance_max_w20")
_LEVELS = (75, 75, 25, 25, 50)
_NAMES = ("DT", "ET", "DB", "EB", "WB")


def candidate_specs() -> tuple[tuple[str, tuple[int, ...]], ...]:
    specs = [("CENTER", _LEVELS)]
    for i, name in enumerate(_NAMES):
        for suffix, delta in (("MINUS", -10), ("PLUS", 10)):
            levels = list(_LEVELS)
            levels[i] += delta
            specs.append((name + "_" + suffix, tuple(levels)))
    return tuple(specs)


def _hash_text(value: str, where: str) -> None:
    if (not isinstance(value, str) or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)):
        raise ValueError(where + ": explicit lowercase SHA256 required")


def balanced_quantile(values_by_market: dict[str, tuple[float, ...]], *, percent: int) -> float:
    """Inverse empirical CDF with exact 1/(6*n_market) weights; no interpolation."""
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError("quantile percent must be an integer in [0,100]")
    if set(values_by_market) != set(MARKETS):
        raise ValueError("quantile requires exactly six markets")
    masses = {}
    for market in MARKETS:
        values = values_by_market[market]
        if not values:
            raise ValueError("empty market: " + market)
        weight = Fraction(1, len(MARKETS) * len(values))
        for value in values:
            try:
                valid = type(value) in (int, float) and isfinite(value)
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError("quantile requires finite non-boolean numbers")
            masses[value] = masses.get(value, Fraction(0)) + weight
    cutoff = Fraction(percent, 100)
    cumulative = Fraction(0)
    for value, weight in sorted(masses.items()):
        cumulative += weight
        if cumulative >= cutoff:
            return value
    raise RuntimeError("quantile mass did not sum to one")


def prepare_quantile_candidates(
    manifest_bytes: bytes, accepted_bytes: bytes, exclusion_bytes: bytes, *,
    trusted_manifest_sha256: str, experiment_id: str, procedure_sha256: str,
    max_disagreement_percent: int,
) -> bytes:
    """Freeze numerical candidates before any classification; never writes files."""
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("nonempty experiment_id required")
    _hash_text(procedure_sha256, "procedure_sha256")
    if type(max_disagreement_percent) is not int or not 0 <= max_disagreement_percent <= 100:
        raise ValueError("explicit disagreement percent within [0,100] required")
    data = validate_d1_research_dataset(
        manifest_bytes, accepted_bytes, exclusion_bytes,
        trusted_manifest_sha256=trusted_manifest_sha256,
    )
    by_market = {m: [] for m in MARKETS}
    for row in data.rows:
        values = dict(row.features.features)
        by_market[row.market].append((
            abs(values["d20_atr"].value), values["er20"].value, values["w20_atr"].value,
        ))
    missing = [m for m in MARKETS if not by_market[m]]
    candidates = []
    for name, levels in candidate_specs():
        parameters, fingerprint, reason = None, None, None
        if missing:
            reason = "EMPTY_MARKETS:" + ",".join(missing)
        else:
            raw = {"hypothesis_id": experiment_id + ":" + name}
            for field, level, feature_index in zip(_FIELDS, levels, (0, 1, 0, 1, 2)):
                series = {m: tuple(row[feature_index] for row in by_market[m]) for m in MARKETS}
                raw[field] = balanced_quantile(series, percent=level)
            parameters = raw
            try:
                fingerprint = RegimeParameters(**raw).fingerprint
            except ValueError as exc:
                reason = str(exc)
        candidates.append({
            "candidate_id": name, "quantile_levels": list(levels),
            "status": "INVALID" if reason else "VALID",
            "parameters": parameters, "parameter_fingerprint": fingerprint,
            "invalid_reason": reason,
        })
    payload = {
        "schema": EXPERIMENT_VERSION, "stage": "PARAMETERS_FROZEN",
        "procedure_status": "UNAPPROVED_PROPOSAL_SIMULATION",
        "experiment_id": experiment_id, "procedure_sha256": procedure_sha256,
        "trusted_manifest_sha256": data.manifest_sha256,
        "accepted_rows_sha256": data.accepted_rows_sha256,
        "exclusion_rows_sha256": data.exclusion_rows_sha256,
        "feature_version": D1_RESEARCH_FEATURE_VERSION, "regime_version": REGIME_VERSION,
        "weighting": "EQUAL_MARKET_EXACT_RATIONAL_INVERSE_CDF",
        "max_disagreement_percent": max_disagreement_percent,
        "budget": 11, "candidates": candidates,
    }
    return (canonical_json(payload) + "\n").encode()


def _ratio(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def _label(row: dict) -> str:
    result = row["regime"]
    return result["state"] + "_" + result["direction"] if result["state"] == "TREND" else result["state"]


def _summarize(rows: list[dict]) -> dict:
    counts = Counter(_label(row) for row in rows)
    chains = []
    transitions = 0
    previous = None
    for row in sorted(rows, key=lambda item: item["input"]["session"]):
        raw = row["input"]
        label = _label(row)
        linked = (
            previous is not None
            and raw["secid"] == previous["input"]["secid"]
            and previous["input"]["expected_sessions"][1:] == raw["expected_sessions"][:-1]
        )
        if linked and label == _label(previous):
            chains[-1]["length"] += 1
        else:
            if linked:
                transitions += 1
            chains.append({"label": label, "length": 1})
        previous = row
    return {
        "accepted_rows": len(rows),
        "classes": {label: counts[label] for label in ("TREND_UP", "TREND_DOWN", "BALANCE", "UNKNOWN")},
        "class_fractions": {label: _ratio(counts[label], len(rows))
                            for label in ("TREND_UP", "TREND_DOWN", "BALANCE", "UNKNOWN")},
        "meaningful_coverage": _ratio(len(rows) - counts["UNKNOWN"], len(rows)),
        "linked_transitions": transitions, "runs": chains,
    }


def _compare(center: list[str], other: list[str]) -> dict:
    if len(center) != len(other):
        raise ValueError("comparison requires identical admitted rows")
    pairs = Counter(zip(center, other))
    differences = sum(a != b for a, b in zip(center, other))
    union = sum(a != "UNKNOWN" or b != "UNKNOWN" for a, b in zip(center, other))
    return {
        "pairs": {a + ">" + b: n for (a, b), n in sorted(pairs.items())},
        "disagreement_all": _ratio(differences, len(center)),
        "disagreement_meaningful_union": _ratio(differences, union),
        "center_coverage": _ratio(sum(x != "UNKNOWN" for x in center), len(center)),
        "other_coverage": _ratio(sum(x != "UNKNOWN" for x in other), len(other)),
    }


def evaluate_frozen_candidates(
    manifest_bytes: bytes, accepted_bytes: bytes, exclusion_bytes: bytes, frozen_bytes: bytes, *,
    trusted_manifest_sha256: str, trusted_frozen_sha256: str,
    max_disagreement_percent: int,
) -> bytes:
    """Evaluate the frozen simulation or return an explicitly aborted report.

    The percentage is mandatory and is a proposal input, not an approved gate.
    No future outcomes, confidence intervals or predictor quality are evaluated.
    """
    _hash_text(trusted_frozen_sha256, "trusted_frozen_sha256")
    if sha256(frozen_bytes) != trusted_frozen_sha256:
        raise ValueError("trusted frozen parameter SHA mismatch")
    if type(max_disagreement_percent) is not int or not 0 <= max_disagreement_percent <= 100:
        raise ValueError("explicit disagreement percent within [0,100] required")
    data = validate_d1_research_dataset(
        manifest_bytes, accepted_bytes, exclusion_bytes,
        trusted_manifest_sha256=trusted_manifest_sha256,
    )
    frozen = _decode(frozen_bytes, "frozen candidates")
    if (frozen.get("schema") != EXPERIMENT_VERSION or frozen.get("stage") != "PARAMETERS_FROZEN"
            or frozen.get("procedure_status") != "UNAPPROVED_PROPOSAL_SIMULATION"
            or frozen.get("feature_version") != D1_RESEARCH_FEATURE_VERSION
            or frozen.get("regime_version") != REGIME_VERSION
            or frozen.get("trusted_manifest_sha256") != data.manifest_sha256
            or frozen.get("accepted_rows_sha256") != data.accepted_rows_sha256
            or frozen.get("exclusion_rows_sha256") != data.exclusion_rows_sha256
            or type(frozen.get("max_disagreement_percent")) is not int
            or frozen["max_disagreement_percent"] != max_disagreement_percent
            or type(frozen.get("budget")) is not int or frozen["budget"] != 11
            or frozen.get("weighting") != "EQUAL_MARKET_EXACT_RATIONAL_INVERSE_CDF"):
        raise ValueError("frozen procedure/data/version mismatch")
    _hash_text(frozen.get("procedure_sha256"), "procedure_sha256")
    experiment_id = frozen.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("frozen experiment identity missing")
    candidates = frozen.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 11:
        raise ValueError("exact frozen budget of eleven candidates required")
    checked = []
    for candidate, (name, levels) in zip(candidates, candidate_specs()):
        if not isinstance(candidate, dict) or candidate.get("candidate_id") != name or candidate.get("quantile_levels") != list(levels):
            raise ValueError("frozen candidate identity/levels mismatch")
        if candidate.get("status") == "VALID":
            raw = candidate.get("parameters")
            if not isinstance(raw, dict) or raw.get("hypothesis_id") != experiment_id + ":" + name:
                raise ValueError("candidate parameter identity mismatch")
            parameters = RegimeParameters(**raw)
            if candidate.get("parameter_fingerprint") != parameters.fingerprint or candidate.get("invalid_reason") is not None:
                raise ValueError("candidate fingerprint/status mismatch")
        elif candidate.get("status") == "INVALID":
            reason = candidate.get("invalid_reason")
            if not isinstance(reason, str) or not reason.strip() or candidate.get("parameter_fingerprint") is not None:
                raise ValueError("invalid candidate needs explicit reason and no fingerprint")
            raw = candidate.get("parameters")
            if raw is None:
                empty = [m for m in MARKETS if not any(r.market == m for r in data.rows)]
                if not empty or reason != "EMPTY_MARKETS:" + ",".join(empty):
                    raise ValueError("invalid candidate empty-market claim mismatch")
            else:
                try:
                    RegimeParameters(**raw)
                except ValueError as exc:
                    if reason != str(exc):
                        raise ValueError("invalid parameter reason mismatch") from exc
                else:
                    raise ValueError("valid parameters mislabeled invalid")
            parameters = None
        else:
            raise ValueError("unsupported candidate status")
        checked.append(parameters)
    attempts, successful = [], {}
    aborted = checked[0] is None
    abort_reason = "INVALID_CENTER" if aborted else None
    for candidate, parameters in zip(candidates, checked):
        name = candidate["candidate_id"]
        entry = {"candidate_id": name, "parameters": candidate["parameters"],
                 "parameter_fingerprint": candidate["parameter_fingerprint"]}
        if parameters is None:
            entry.update(status="INVALID_PARAMETERS", reason=candidate["invalid_reason"])
        elif aborted:
            entry.update(status="NOT_RUN", reason=abort_reason)
        else:
            try:
                result = json.loads(run_d1_descriptive_research(
                    manifest_bytes, accepted_bytes, exclusion_bytes,
                    trusted_manifest_sha256=trusted_manifest_sha256, parameters=parameters,
                ))
                if result.get("completion") != "COMPLETE":
                    raise ValueError("runner returned an incomplete result")
            except (ValueError, TypeError, KeyError) as exc:
                aborted = True
                abort_reason = "INTERNAL_EVALUATION_FAILURE"
                entry.update(status="FAILED", reason=str(exc))
            else:
                successful[name] = result["results"]
                entry.update(status="COMPLETE", result=result,
                             summaries={m: {
                                 "all": _summarize([r for r in result["results"] if r["input"]["market"] == m]),
                                 "by_year": {y: _summarize([r for r in result["results"]
                                                if r["input"]["market"] == m and r["input"]["session"][:4] == y])
                                             for y in ("2021", "2022")},
                             } for m in MARKETS})
        attempts.append(entry)
    comparisons, baseline, summaries, selection_reasons = {}, {}, {}, []
    if aborted:
        selection_reasons.append(abort_reason)
    if any(p is None for p in checked):
        selection_reasons.append("INVALID_CANDIDATES")
    if not aborted:
        center_rows = successful["CENTER"]
        for market in MARKETS:
            rows = [r for r in center_rows if r["input"]["market"] == market]
            labels = [_label(r) for r in rows]
            summaries[market] = {
                "all": _summarize(rows),
                "by_year": {year: _summarize([r for r in rows if r["input"]["session"][:4] == year])
                            for year in ("2021", "2022")},
            }
            if len(set(labels)) <= 1:
                selection_reasons.append("DEGENERATE_CENTER:" + market)
            market_comparisons = {}
            for name in successful:
                if name == "CENTER":
                    continue
                other = [_label(r) for r in successful[name] if r["input"]["market"] == market]
                stats = _compare(labels, other)
                market_comparisons[name] = stats
                ratio = stats["disagreement_all"]
                if ratio["denominator"] and Fraction(ratio["numerator"], ratio["denominator"]) > Fraction(max_disagreement_percent, 100):
                    selection_reasons.append("SENSITIVE:" + market + ":" + name)
            comparisons[market] = market_comparisons
            threshold = checked[0].trend_min_abs_d20
            baseline_labels = []
            for row in rows:
                d = row["input"]["features"]["features"]["d20_atr"]["value"]
                baseline_labels.append(("TREND_UP" if d > 0 else "TREND_DOWN") if abs(d) >= threshold else "UNKNOWN")
            baseline[market] = _compare(labels, baseline_labels)
    payload = {
        "schema": EXPERIMENT_VERSION, "completion": "ABORTED" if aborted else "COMPLETE",
        "procedure_status": "UNAPPROVED_PROPOSAL_SIMULATION",
        "scope": "RECONSTRUCTED_RESEARCH_ONLY", "strict_historical_as_known_at_T0": False,
        "frozen_sha256": trusted_frozen_sha256, "frozen_parameters": frozen,
        "input_manifest": json.loads(data.manifest_json),
        "max_disagreement_percent": max_disagreement_percent,
        "attempts": attempts, "center_summaries": summaries,
        "sensitivity": comparisons, "baseline_comparisons": baseline,
        "selection_reasons": selection_reasons,
        "proposed_candidate": "CENTER" if not selection_reasons else None,
        "forecast_admission": False, "predictive_quality_proven": False,
    }
    return (canonical_json(payload) + "\n").encode()
