"""Offline development-only D1 5/10/20 session historical diagnostic.

Usage:
 python -m scripts.g2_d1_historical_diagnostic --archive COPY.duckdb \
     --warmup-v2 WITNESS.json --output-dir SCRATCH/

Never downloads MOEX data or creates a past ForecastRecord. Exact archived
active-root sessions identify NEXT sessions; first receipt at historical T0
remains NOT_PROVEN. This is not strict PIT, OOS validation or LIVE issuance.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path

from scripts.g2_d1_development_dataset import (
    DEVELOPMENT_START, DEVELOPMENT_END, EXTRACTOR_VERSION,
    MARKETS, _active_calendar, _canonical, _hash, _root_key,
    _validate_schema, load_v2, make_dataset,
)

SCHEMA = "G2_D1_HISTORICAL_DEVELOPMENT_DIAGNOSTIC_V1"
HORIZONS = (5, 10, 20)
METHOD = "PREDECLARED_D20_ATR_SIGN_V1"  # independent of Forecast V1


def _complete_close(db, secid: str, day: str) -> float | None:
    """Refuse duplicate, incomplete or invalid exact SECID D1 rows."""
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    rows = db.execute("""
        SELECT begin,end_time,open,high,low,close,completed
        FROM historical_candles
        WHERE timeframe='D1' AND secid=? AND begin>=? AND begin<?
        ORDER BY begin
    """, [secid, day, next_day]).fetchall()
    if len(rows) != 1:
        return None
    begin, end, opening, high, low, close, completed = rows[0]
    # A boolean completion flag and a matching day are not sufficient:
    # reversed/equal event times or >24h candles cannot prove a completed D1.
    try:
        started = datetime.fromisoformat(str(begin))
        finished = datetime.fromisoformat(str(end))
        elapsed = finished - started
    except (TypeError, ValueError):
        return None
    nums = (opening, high, low, close)
    if (str(begin)[:10] != day or str(end)[:10] not in
        (day, next_day) or not timedelta(0) < elapsed <= timedelta(days=1)
        or not bool(completed)
        or any(type(x) not in (int, float) or not isfinite(x)
               or x <= 0 for x in nums)
        or not low <= min(opening, close) <= max(opening, close) <= high):
        return None
    return float(close)


def evaluate_historical_development(db, reconstructed_rows: list[dict],
                                    *, till_day: str = DEVELOPMENT_END):
    """Read ONLY 2021-22 archive. No future facts become historical features."""
    if not (DEVELOPMENT_START <= till_day <= DEVELOPMENT_END):
        raise ValueError("historical development target must stay within 2021-22")
    calendars = {
        market: _active_calendar(db, market, through_day=till_day)
        for market in MARKETS
    }
    indices = {market:{day:i for i,(day,secid) in enumerate(calendar)}
               for market,calendar in calendars.items()}
    accepted = []
    excluded = []
    reasons = Counter()
    seen = set()
    for row in reconstructed_rows:
        market, secid, session = row["market"], row["secid"], row["session"]
        if (market not in calendars or not isinstance(secid,str)
            or not (DEVELOPMENT_START <= session <= till_day)
            or row.get("historical_first_receipt") != "NOT_PROVEN"
            or row.get("decision_knowledge_cutoff_at") is not None
            or row.get("source") != "HISTORICAL_ARCHIVE_RECONSTRUCTED"
            or row.get("features",{}).get("eligibility") != "RECONSTRUCTED_RESEARCH_ONLY"):
            raise ValueError("row is outside allowed reconstructed historical Development")
        token=(market,secid,session)
        if token in seen:
            raise ValueError("duplicate reconstructed development feature row")
        seen.add(token)
        index=indices[market].get(session)
        if index is None or calendars[market][index][1]!=secid:
            raise ValueError("reconstructed row not on exact archived root SECID")
        feat=row["features"]["features"]["d20_atr"]
        if feat["status"]!="AVAILABLE" or type(feat["value"]) not in (int,float) or not isfinite(feat["value"]):
            raise ValueError("a historical diagnostic must use available predeclared feature")
        # Forecast rule fixed before seeing future candles; it is only a
        # D1 diagnostic baseline, NOT the existing Forecast V1 algorithm.
        direction="UP" if feat["value"]>0 else "DOWN" if feat["value"]<0 else "NEUTRAL"
        reference=_complete_close(db,secid,session)
        if reference is None:
            raise ValueError("historical input reference D1 was invalid or changed")
        for horizon in HORIZONS:
            target=calendars[market][index+1:index+horizon+1]
            reason=None
            if len(target)!=horizon or target[-1][0]>till_day:
                reason="FUTURE_OUTSIDE_DEVELOPMENT"
            elif any(contract!=secid for _,contract in target):
                reason="CONTRACT_ROLL_INSIDE_FUTURE_WINDOW"
            else:
                # Check all original future closes, not just the terminal
                # price: no silent gaps or switching of SECID.
                future=[_complete_close(db,secid,day) for day,_ in target]
                if any(price is None for price in future):
                    reason="MISSING_OR_INVALID_FUTURE_D1"
            if reason:
                excluded.append({"market":market,"secid":secid,"session":session,
                                 "horizon":horizon,"reason":reason})
                reasons[reason]+=1
                continue
            last=float(future[-1])
            realized=round((last/reference-1.0)*100.0,8)
            success=(realized>0 if direction=="UP" else
                     realized<0 if direction=="DOWN" else None)
            accepted.append({
                "market":market,"secid":secid,"session":session,
                "horizon":horizon,
                "baseline":METHOD,"direction":direction,
                "reference_close":reference,"target_close":last,
                "future_sessions":[day for day,_ in target],
                "target_session":target[-1][0],
                "actual_return_pct":realized,"direction_hit":success,
                "retrospective_reconstructed_only":True,
                "historical_first_receipt":"NOT_PROVEN",
                "strict_historical_as_known_at_T0":False,
                "Forecast_V1_measured":False,
                "official_calendar_independently_authenticated":False,
            })
    if len(accepted)+len(excluded)!=len(reconstructed_rows)*len(HORIZONS):
        raise RuntimeError("one exact result per historical candidate and horizon required")
    return accepted, excluded, dict(sorted(reasons.items()))


def run(archive: Path, warmup_v2: Path, output_dir: Path):
    if not archive.is_file() or not warmup_v2.is_file():
        raise FileNotFoundError("read-only historical archive and V2 evidence required")
    if archive.resolve().is_relative_to(output_dir.resolve()):
        raise ValueError("output directory must not contain historical input archive")
    original_sha=_hash(archive)
    evidence=load_v2(warmup_v2,original_sha)
    import duckdb
    db=duckdb.connect(str(archive),read_only=True)
    try:
        caps=_validate_schema(db)
        rows, exclusions, totals=make_dataset(db,evidence,has_caps=caps)
        results,rejected,reasons=evaluate_historical_development(db,rows)
    finally:
        db.close()
    if _hash(archive)!=original_sha:
        raise RuntimeError("historical source changed during offline evaluation")
    data=[
        ("g2_d1_historical_results.jsonl",
         "".join(_canonical(x)+"\n" for x in results).encode()),
        ("g2_d1_historical_exclusions.jsonl",
         "".join(_canonical(x)+"\n" for x in rejected).encode()),
    ]
    manifest={
        "schema":SCHEMA,"scope":"RECONSTRUCTED_HISTORICAL_DEVELOPMENT_ONLY",
        "original_archive_sha256":original_sha,
        "warmup_v2_sha256":_hash(warmup_v2),
        "extractor_schema":EXTRACTOR_VERSION,
        "baseline":METHOD,
        "period":[DEVELOPMENT_START,DEVELOPMENT_END],
        "protected_2023_2024_holdout_opened":False,
        "live_moex_calls":False,"Forecast_V1_measured":False,
        "historical_first_receipt":"NOT_PROVEN",
        "strict_historical_as_known_at_T0":False,
        "independent_market_calendar_verified":False,
        "retrospective_predictive_skill_certified":False,
        "accepted_feature_rows":len(rows),
        "pre_outcome_exclusion_rows":len(exclusions),
        "candidate_horizon_pairs":len(rows)*len(HORIZONS),
        "evaluated_horizon_pairs":len(results),
        "excluded_horizon_pairs":len(rejected),
        "exclusions_by_reason":reasons,
        "per_market":totals,
        "results_sha256":sha256(data[0][1]).hexdigest(),
        "exclusions_sha256":sha256(data[1][1]).hexdigest(),
        "script_sha256":_hash(Path(__file__).resolve()),
        "release_allowed":False,
    }
    data.append(("g2_d1_historical_manifest.json",(_canonical(manifest)+"\n").encode()))
    # Preflight all output destinations before making any writes. Never
    # replace a differing user artifact even after partial prior attempts.
    output_dir.mkdir(parents=True,exist_ok=True)
    # Preflight regular files and conflicting directories/symlinks before
    # committing any of the three result artifacts.
    for name, body in data:
        target = output_dir / name
        if target.is_symlink() or (
            target.exists() and (
                not target.is_file() or target.read_bytes() != body
            )
        ):
            raise FileExistsError("refusing to replace an unlike historical result")
    for name,body in data:
        target=output_dir/name
        if not target.exists():
            import os, tempfile
            with tempfile.NamedTemporaryFile(dir=output_dir,delete=False,
                                            prefix="g2_hist_") as handle:
                temp=Path(handle.name)
                handle.write(body)
            try:
                os.replace(temp,target)
            finally:
                temp.unlink(missing_ok=True)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--warmup-v2",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(run(args.archive,args.warmup_v2,args.output_dir),
                     ensure_ascii=False,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
