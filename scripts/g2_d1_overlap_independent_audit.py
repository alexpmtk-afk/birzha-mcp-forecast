"""Independent 2021–22 reconstructed D5-sign Development diagnostic.

Offline only. Reads a SHA-matched existing derived feature file. Does not
open 2023–24 OOS, MOEX providers, original DuckDB or Forecast V1.
Non-overlap: for each market and horizon, greedily keep the first scored
origin whose T0 is at/after the preceding retained target session.
Descriptive sampling convention, NOT an independence/significance test.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from scripts.g2_first_forecast_development import (
    EXPERIMENT_VERSION, FEATURE_VERSION, HORIZONS, MARKETS,
    _write_immutable, load_rows, predict, score, sha, stable_json,
)

AUDIT_SCHEMA = "G2_D5_SIGN_DEVELOPMENT_OVERLAP_AUDIT_V1"


def _verify_manifest(input_path: Path, manifest_path: Path) -> tuple[dict, str]:
    source_hash = sha(input_path)
    report = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        report.get("schema") != "G2_SIX_MARKET_D1_DEVELOPMENT_DATASET_V1"
        or report.get("scope") != "RECONSTRUCTED_RESEARCH_ONLY"
        or report.get("strict_historical_as_known_at_T0") is not False
        or tuple(report.get("markets", ())) != MARKETS
        or report.get("development_from") != "2021-01-01"
        or report.get("development_till") != "2022-12-31"
        or report.get("feature_version") != FEATURE_VERSION
        or report.get("accepted_rows_sha256") != source_hash
    ):
        raise ValueError("Development feature file and manifest do not agree")
    return report, source_hash


def _stats(records: list[dict]) -> dict:
    scored = [x for x in records
              if x.get("correct") is not None and
              x.get("comparator_correct") is not None]
    hits = sum(int(x["correct"]) for x in scored)
    cmp_hits = sum(int(x["comparator_correct"]) for x in scored)
    return {
        "candidates":len(records), "scored":len(scored),
        "d5_sign_hits":hits,"always_up_hits_on_same_rows":cmp_hits,
        "d5_sign_accuracy":hits/len(scored) if scored else None,
        "always_up_accuracy":cmp_hits/len(scored) if scored else None,
    }


def _nonoverlap(records: list[dict], market: str, horizon: int) -> list[dict]:
    scored = sorted(
        (r for r in records
         if r["market"]==market and r["horizon_sessions"]==horizon
         and r.get("correct") is not None and r.get("target_session")),
        key=lambda r:(r["origin_session"],r["secid"]),
    )
    kept=[]
    previous_target=None
    for item in scored:
        if previous_target is None or item["origin_session"] >= previous_target:
            if item["target_session"]<=item["origin_session"]:
                raise ValueError("future session is not strictly after T0")
            kept.append(item)
            previous_target=item["target_session"]
    return kept


def run(dataset: Path, manifest: Path, output: Path) -> dict:
    dataset=Path(dataset);manifest=Path(manifest);output=Path(output)
    feature_meta,source_sha=_verify_manifest(dataset,manifest)
    rows=load_rows(dataset)
    if len(rows)!=feature_meta.get("accepted_rows"):
        raise ValueError("Development manifest candidate count differs")
    # Predictor is origin-only. Outcomes are reconstructed in a separate
    # later call; neither is Forecast V1 nor strict historical PIT.
    predictions=predict(rows)
    if sha(dataset)!=source_sha:
        raise RuntimeError("Development source changed after predictions")
    outcomes,_existing_summary=score(predictions,rows)
    if sha(dataset)!=source_sha:
        raise RuntimeError("Development source changed after future scoring")
    if len(outcomes)!=len(rows)*len(HORIZONS):
        raise RuntimeError("not exactly 5/10/20 results per Development origin")
    overlapping={str(h):_stats([r for r in outcomes
                                 if r["horizon_sessions"]==h])
                 for h in HORIZONS}
    by_market={
        market:{str(h):_stats([r for r in outcomes
                 if r["market"]==market and r["horizon_sessions"]==h])
                for h in HORIZONS}
        for market in MARKETS
    }
    by_origin_year={
        year:{str(h):_stats([r for r in outcomes
             if r["origin_session"].startswith(year)
             and r["horizon_sessions"]==h])
              for h in HORIZONS}
        for year in ("2021","2022")
    }
    nonoverlap={
        str(h):_stats([
            r for market in MARKETS
              for r in _nonoverlap(outcomes,market,h)
        ])
        for h in HORIZONS
    }
    result={
        "schema":AUDIT_SCHEMA,
        "scope":"RECONSTRUCTED_DEVELOPMENT_2021_2022_ONLY",
        "original_dataset_sha256":source_sha,
        "dataset_manifest_sha256":sha(manifest),
        "original_archive_sha256_manifest_claim":feature_meta.get(
            "source_sha256_before_after"),
        "candidate_feature_rows":len(rows),
        "experiment_version":EXPERIMENT_VERSION,
        "method":"D5_SIGN_ZERO_FIT_V1",
        "Forecast_V1_measured":False,
        "original_historical_first_receipt":"NOT_PROVEN",
        "strict_historical_as_known_at_T0":False,
        "untouched_2023_2024_holdout_opened":False,
        "no_externally_certified_independent_sessions":True,
        "release_allowed":False,
        "overlapping":overlapping,
        "nonoverlapping_greedy_per_market":nonoverlap,
        "by_market":by_market,
        "by_origin_year":by_origin_year,
        "unscored_reasons":dict(sorted(Counter(
            str(x["horizon_sessions"])+":"+x["outcome_status"]
            for x in outcomes if x["outcome_status"]!="SCORED"
        ).items())),
        "caveats":[
            "Not Forecast V1, not strict PIT, not true source-vintage proof",
            "Overlapping observations cannot be counted as independent",
            "Greedy nonoverlap is one deterministic sub-sample, not statistical proof",
            "By-year outcomes are descriptive, not an untouched holdout",
            "Do not tune or select a final model based on protected 2023-24",
        ],
    }
    if sha(dataset)!=source_sha:
        raise RuntimeError("Development source modified before report write")
    output.parent.mkdir(parents=True,exist_ok=True)
    _write_immutable(output,(stable_json(result)+"\n").encode("utf-8"))
    return result


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset",type=Path,required=True)
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    print(json.dumps(run(a.dataset,a.manifest,a.output),
                     ensure_ascii=False,sort_keys=True,indent=2))


if __name__=="__main__":
    main()
