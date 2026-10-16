"""Audit H1 duration-normalization loss in frozen reconstructed research inputs.

Forensic/read-only: never repairs raw candles or promotes a bar to completed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import zipfile

INPUT_ENTRY = "allowed-real-run-v2/inputs.json"
RECEIPT_ENTRY = "allowed-real-run-v2/execution-receipt.json"
INPUT_SCHEMA = "G2_PRICE_REPLAY_INPUT_BUNDLE_V1"
REPORT_SCHEMA = "G2_H1_DURATION_FORENSICS_V1"


def _stamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime required")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def audit_bundle(bundle: dict, receipt: dict) -> dict:
    """Compare flagged bars with receipt. Any disagreement fails closed."""
    if bundle.get("schema") != INPUT_SCHEMA or bundle.get("scope") != "RECONSTRUCTED_HISTORICAL_PRICE_ENGINE_ONLY":
        raise ValueError("wrong research bundle schema or scope")
    if not isinstance(bundle.get("cases"), list):
        raise ValueError("cases missing")
    counts_by_case = {
        (x["case_id"], x["timeframe"]): x["incomplete_duration_marked"]
        for x in receipt["duration_normalization"]
    }
    if len(counts_by_case) != len(receipt["duration_normalization"]):
        raise ValueError("duplicate receipt cases")
    combined = Counter()
    by_market: dict[str, Counter] = {}
    case_details = []
    seen = set()
    for case in bundle["cases"]:
        case_id = case["case_id"]
        if case_id in seen:
            raise ValueError("duplicate case_id")
        seen.add(case_id)
        instrument = case["instrument"]
        market = instrument["symbol"]
        rows = case["series"]["H1"]
        if not isinstance(rows, list):
            raise ValueError("H1 rows not list")
        counts = Counter()
        previous_start = None
        for i, candle in enumerate(rows):
            start, end = _stamp(candle["begin"]), _stamp(candle["end"])
            if end <= start or (previous_start is not None and start <= previous_start):
                raise ValueError("invalid/unsorted H1 rows")
            previous_start = start
            duration = (end - start).total_seconds()
            if type(candle["completed"]) is not bool:
                raise ValueError("non-boolean completed")
            if candle["completed"]:
                counts["completed_true"] += 1
                if duration not in {3599.0, 3600.0}:
                    raise ValueError("completed H1 conflicts with recorded duration rule")
                continue
            counts["flagged_false"] += 1
            if duration in {3599.0, 3600.0}:
                raise ValueError("false H1 not explained by duration reflagging")
            counts["false_from_duration_rule"] += 1
            nxt = rows[i+1] if i+1 < len(rows) else None
            if nxt is None:
                counts["no_next_row_in_context"] += 1
            else:
                next_start = _stamp(nxt["begin"])
                if next_start.date() != start.date():
                    counts["next_on_later_date"] += 1
                elif next_start >= start + timedelta(hours=1):
                    counts["later_h1_same_date"] += 1
                else:
                    counts["next_same_date_not_full_hour"] += 1
            if market in {"IMOEX", "RTSI"} and start.hour == 18:
                counts["index_18_hour"] += 1
            elif market == "SBER" and start.hour in {18, 23}:
                counts["sber_18_or_23_hour"] += 1
            elif market in {"Si", "BR"} and start.hour == 23:
                counts["futures_23_hour"] += 1
            else:
                counts["other_hours"] += 1
        if counts_by_case.get((case_id, "H1")) != counts["false_from_duration_rule"]:
            raise ValueError(f"receipt does not support normalization count: {case_id}")
        counts["total_rows"] = len(rows)
        counts["tail50_false"] = sum(not r["completed"] for r in rows[-50:])
        if len(rows) < 50:
            status = "INSUFFICIENT_50_ROWS"
        elif counts["tail50_false"]:
            status = "DURATION_FLAGGED_IN_LAST_50"
        else:
            status = "LAST_50_UNFLAGGED"
        combined.update(counts)
        by_market.setdefault(market, Counter()).update(counts)
        case_details.append({
            "case_id": case_id, "market": market, "secid": instrument["secid"],
            "window_status": status, "counts": dict(counts),
        })
    return {
        "schema": REPORT_SCHEMA,
        "cases": len(case_details),
        "window_status": dict(Counter(c["window_status"] for c in case_details)),
        "total_counts": dict(combined),
        "by_market": {m: dict(v) for m, v in by_market.items()},
        "case_details": case_details,
        "original_source_completed_bit_recoverable": False,
        "independent_first_receipt_attested": False,
        "reclassifies_or_mutates_candles": False,
        "all_flagged_bars_known_incomplete": False,
        "admitted_to_forecast": False,
    }


def audit_archive(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        input_bytes = archive.read(INPUT_ENTRY)
        receipt = json.loads(archive.read(RECEIPT_ENTRY))
    actual_hash = hashlib.sha256(input_bytes).hexdigest()
    if actual_hash != receipt.get("input_sha256"):
        raise ValueError("input hash mismatch")
    report = audit_bundle(json.loads(input_bytes), receipt)
    report["source_archive_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["input_bundle_sha256"] = actual_hash
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_archive(args.archive)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "cases": result["cases"],
        "window_status": result["window_status"],
        "total_counts": result["total_counts"],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
