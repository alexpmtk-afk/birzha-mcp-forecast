"""Direct arithmetic cross-check of complete descriptive experiment reports.

Uses only the standard library: never imports production regime/quantile code.
Not a data-admission replacement or independent human acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path

MARKETS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
CLASSES = ("TREND_UP", "TREND_DOWN", "BALANCE", "UNKNOWN")
FIELDS = ("trend_min_abs_d20", "trend_min_er20", "balance_max_abs_d20",
          "balance_max_er20", "balance_max_w20")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("nonstandard JSON number: " + value)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def quantile(rows, feature, percent):
    counts = Counter(r["market"] for r in rows)
    require(set(counts) == set(MARKETS), "six nonempty markets required")
    weighted = []
    for row in rows:
        value = row["features"]["features"][feature]["value"]
        if feature == "d20_atr":
            value = abs(value)
        weighted.append((value, Fraction(1, 6 * counts[row["market"]])))
    mass = Fraction(0)
    for value, weight in sorted(weighted):
        mass += weight
        if mass >= Fraction(percent, 100):
            return value
    raise ValueError("quantile not reached")


def direct_label(row, parameters):
    features = row["features"]["features"]
    d, er, width = (features[name]["value"] for name in ("d20_atr", "er20", "w20_atr"))
    if abs(d) >= parameters[FIELDS[0]] and er >= parameters[FIELDS[1]]:
        return "TREND_UP" if d > 0 else "TREND_DOWN"
    if abs(d) <= parameters[FIELDS[2]] and er <= parameters[FIELDS[3]] and width <= parameters[FIELDS[4]]:
        return "BALANCE"
    return "UNKNOWN"


def ratio(n, d):
    return {"numerator": n, "denominator": d, "value": n / d if d else None}


def comparison(left, right):
    require(len(left) == len(right), "comparison row mismatch")
    pairs = Counter(zip(left, right))
    n = sum(a != b for a, b in zip(left, right))
    union = sum(a != "UNKNOWN" or b != "UNKNOWN" for a, b in zip(left, right))
    return {
        "pairs": {a + ">" + b: count for (a, b), count in sorted(pairs.items())},
        "disagreement_all": ratio(n, len(left)),
        "disagreement_meaningful_union": ratio(n, union),
        "center_coverage": ratio(sum(a != "UNKNOWN" for a in left), len(left)),
        "other_coverage": ratio(sum(b != "UNKNOWN" for b in right), len(right)),
    }


def summary(rows, labels):
    counts = Counter(labels)
    ordered = sorted(zip(rows, labels), key=lambda item: item[0]["session"])
    runs, switches = [], 0
    for index, (row, label) in enumerate(ordered):
        linked = False
        if index:
            previous, previous_label = ordered[index-1]
            linked = (row["secid"] == previous["secid"]
                      and previous["expected_sessions"][1:] == row["expected_sessions"][:-1])
            switches += int(linked and label != previous_label)
        if linked and label == ordered[index-1][1]:
            runs[-1]["length"] += 1
        else:
            runs.append({"label": label, "length": 1})
    return {"accepted_rows": len(rows), "classes": {name: counts[name] for name in CLASSES},
            "class_fractions": {name: ratio(counts[name], len(rows)) for name in CLASSES},
            "meaningful_coverage": ratio(len(rows)-counts["UNKNOWN"], len(rows)),
            "linked_transitions": switches, "runs": runs}


def verify(manifest_bytes, accepted_bytes, excluded_bytes, frozen_bytes, report_bytes):
    manifest, frozen, report = map(decode, (manifest_bytes, frozen_bytes, report_bytes))
    rows = [decode(line) for line in accepted_bytes.splitlines()]
    excluded = [decode(line) for line in excluded_bytes.splitlines()]
    require(frozen["trusted_manifest_sha256"] == digest(manifest_bytes), "manifest hash")
    require(frozen["accepted_rows_sha256"] == digest(accepted_bytes), "accepted hash")
    require(frozen["exclusion_rows_sha256"] == digest(excluded_bytes), "excluded hash")
    require(report["frozen_sha256"] == digest(frozen_bytes), "frozen hash")
    require(report["frozen_parameters"] == frozen and report["input_manifest"] == manifest, "embedded input")
    require(report["completion"] == "COMPLETE", "complete report required for this checker")
    require(report["forecast_admission"] is False and report["predictive_quality_proven"] is False, "closed forecast admission")
    require(frozen["budget"] == 11 and frozen["max_disagreement_percent"] == report["max_disagreement_percent"] == 20, "fixed budget/criterion")
    require(len(rows) == manifest["accepted_rows"] and len(excluded) == manifest["excluded_rows"], "dataset totals")
    require(len({(r["market"], r["session"]) for r in rows}) == len(rows), "unique inputs")
    specs = [("CENTER", (75, 75, 25, 25, 50))]
    for index, name in enumerate(("DT", "ET", "DB", "EB", "WB")):
        for suffix, delta in (("MINUS", -10), ("PLUS", 10)):
            levels = [75, 75, 25, 25, 50]
            levels[index] += delta
            specs.append((name + "_" + suffix, tuple(levels)))
    require(len(frozen["candidates"]) == len(report["attempts"]) == 11, "attempt count")
    labels_by_candidate, parameters_by_candidate = {}, {}
    for candidate, attempt, (name, levels) in zip(frozen["candidates"], report["attempts"], specs):
        require(candidate["candidate_id"] == attempt["candidate_id"] == name, "candidate order")
        require(candidate["status"] == "VALID" and attempt["status"] == "COMPLETE", "complete valid candidates required")
        require(candidate["quantile_levels"] == list(levels), "quantile levels")
        expected = {"hypothesis_id": frozen["experiment_id"] + ":" + name}
        for field, feature, level in zip(FIELDS, ("d20_atr", "er20", "d20_atr", "er20", "w20_atr"), levels):
            expected[field] = quantile(rows, feature, level)
        require(candidate["parameters"] == attempt["parameters"] == expected, "direct quantile parameters")
        fingerprint_payload = {key: value if key == "hypothesis_id" else float(value) for key, value in expected.items()}
        fingerprint_payload["regime_version"] = "G2_D1_DESCRIPTIVE_REGIME_V1"
        fingerprint = digest(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
        require(candidate["parameter_fingerprint"] == attempt["parameter_fingerprint"] == fingerprint, "parameter fingerprint")
        result = attempt["result"]
        require(result["completion"] == "COMPLETE" and result["parameters"] == expected
                and result["parameter_fingerprint"] == fingerprint, "runner parameters")
        require(result["accepted_result_count"] == len(rows) and result["upstream_exclusions"] == excluded
                and result["upstream_exclusion_count"] == len(excluded), "runner counts/exclusions")
        require(len(result["results"]) == len(rows), "classification count")
        labels = []
        for raw, classified in zip(rows, result["results"]):
            require(classified["input"] == raw, "classification input/order")
            actual = classified["regime"]
            label = direct_label(raw, expected)
            wanted_state = "TREND" if label.startswith("TREND_") else label
            wanted_direction = label[6:] if wanted_state == "TREND" else None
            require(actual["state"] == wanted_state and actual["direction"] == wanted_direction, "direct classification")
            require(actual["parameters"] == expected and actual["parameter_fingerprint"] == fingerprint, "row parameter identity")
            labels.append(label)
        labels_by_candidate[name], parameters_by_candidate[name] = labels, expected
        for market in MARKETS:
            selected = [i for i, row in enumerate(rows) if row["market"] == market]
            local_rows, local_labels = [rows[i] for i in selected], [labels[i] for i in selected]
            full = summary(local_rows, local_labels)
            wanted = {"all": full, "by_year": {}}
            for year in ("2021", "2022"):
                positions = [i for i in selected if rows[i]["session"][:4] == year]
                wanted["by_year"][year] = summary([rows[i] for i in positions], [labels[i] for i in positions])
            require(attempt["summaries"][market] == wanted, "class/year/run summary")
            if name == "CENTER":
                require(report["center_summaries"][market] == wanted, "center summary")
            source = manifest["per_market"][market]
            excluded_local = [r for r in excluded if r["market"] == market]
            require(len(selected) == source["admitted_reconstructed"] and len(excluded_local) == source["excluded"], "market totals")
            per_market = result["per_market"][market]
            require(per_market["accepted_inputs"] == len(selected) and per_market["upstream_excluded"] == len(excluded_local)
                    and per_market["upstream_exclusion_reasons"] == source["exclusion_reasons"]
                    and per_market["invalid_inputs"] == 0, "runner market admission")
            for label in CLASSES:
                key = "UNKNOWN_TRANSITION" if label == "UNKNOWN" else label
                require(per_market[key] == full["classes"][label], "runner market class counts")
    reasons = []
    for market in MARKETS:
        selected = [i for i, row in enumerate(rows) if row["market"] == market]
        center = [labels_by_candidate["CENTER"][i] for i in selected]
        if len(set(center)) <= 1:
            reasons.append("DEGENERATE_CENTER:" + market)
        for name in labels_by_candidate:
            if name == "CENTER":
                continue
            other = [labels_by_candidate[name][i] for i in selected]
            wanted = comparison(center, other)
            wanted["by_year"] = {year: comparison(
                [labels_by_candidate["CENTER"][i] for i in selected if rows[i]["session"][:4] == year],
                [labels_by_candidate[name][i] for i in selected if rows[i]["session"][:4] == year],
            ) for year in ("2021", "2022")}
            require(report["sensitivity"][market][name] == wanted, "sensitivity/year comparison")
            r = wanted["disagreement_all"]
            if Fraction(r["numerator"], r["denominator"]) > Fraction(1, 5):
                reasons.append("SENSITIVE:" + market + ":" + name)
        baseline = []
        for i in selected:
            d = rows[i]["features"]["features"]["d20_atr"]["value"]
            baseline.append(("TREND_UP" if d > 0 else "TREND_DOWN")
                            if abs(d) >= parameters_by_candidate["CENTER"][FIELDS[0]] else "UNKNOWN")
        wanted = comparison(center, baseline)
        wanted["by_year"] = {year: comparison(
            [a for i, a in zip(selected, center) if rows[i]["session"][:4] == year],
            [b for i, b in zip(selected, baseline) if rows[i]["session"][:4] == year],
        ) for year in ("2021", "2022")}
        require(report["baseline_comparisons"][market] == wanted, "baseline/year comparison")
    require(report["selection_reasons"] == reasons, "selection reasons")
    require(report["proposed_candidate"] == ("CENTER" if not reasons else None), "proposed candidate")
    return {"schema": "G2_DIRECT_REGIME_CONTROL_V1", "checked_by": "IMPLEMENTER_SEPARATE_STANDARD_LIBRARY_ARITHMETIC",
            "independent_human_acceptance": False, "forecast_admission": False,
            "input_rows": len(rows), "excluded_rows": len(excluded), "checked_candidates": 11,
            "checked_classifications": 11 * len(rows), "parameter_fields_checked": 55,
            "report_sha256": digest(report_bytes), "all_comparisons_match": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--frozen-sha256", required=True)
    parser.add_argument("--report-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest, accepted, excluded, frozen = [(args.preparation / name).read_bytes()
        for name in ("manifest.json", "accepted.jsonl", "exclusions.jsonl", "frozen.json")]
    report = args.report.read_bytes()
    for raw, expected in ((manifest, args.manifest_sha256), (frozen, args.frozen_sha256), (report, args.report_sha256)):
        require(digest(raw) == expected, "external hash mismatch")
    result = verify(manifest, accepted, excluded, frozen, report)
    raw = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
    with args.output.open("xb") as stream:
        stream.write(raw)
    print(raw.decode())


if __name__ == "__main__":
    main()
