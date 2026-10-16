"""Run actual G2 Market Structure module on the 30 frozen reconstructed cases.

RESEARCH ONLY; no network, DB, first-receipt reconstruction or Forecast calls.
The exact original completed flag is retained. Naive timestamps are labelled,
not represented as independently measured historical receipt time.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import argparse
import csv
import hashlib
import json
from pathlib import Path
import zipfile

from birzha.application.market_structure_facts import (
    VERSION, build_market_structure_facts, build_reconstructed_market_structure_facts,
)
from birzha.domain.market import Instrument, Candle, CandleSeries

ENTRY = "allowed-real-run-v2/inputs.json"
RECEIPT = "allowed-real-run-v2/execution-receipt.json"
ASSUMPTION = "Europe/Moscow UTC+03:00 2025-2026 (assumption, not source receipt)"
MOSCOW_OFFSET = timezone(timedelta(hours=3))
FIELDS = ("case_id", "symbol", "secid", "t0", "d1_status", "d1_structure",
          "d1_atr_pct", "d1_volume_ratio", "d1_reason", "h1_status",
          "h1_structure", "h1_atr_pct", "h1_volume_ratio", "h1_reason",
          "h1_original_false", "h1_bars", "h1_strict_result",
          "d1_input_sha256", "h1_input_sha256")


def _archive_stamp(value: str) -> str:
    raw = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if raw.tzinfo is not None:
        raise ValueError("frozen naive source format changed")
    return raw.replace(tzinfo=MOSCOW_OFFSET).isoformat()


def _series(case: dict, timeframe: str) -> CandleSeries:
    payload = case["instrument"]
    inst = Instrument(**{**payload, "data_capabilities":
                         tuple(payload["data_capabilities"])})
    bars = tuple(Candle(**{**bar,
                          "begin": _archive_stamp(bar["begin"]),
                          "end": _archive_stamp(bar["end"]),
                          "available_at": None, "observed_at": None,
                          "available_at_confidence": "UNKNOWN",
                          "source": "RECONSTRUCTED_RESEARCH_ONLY"})
                 for bar in case["series"][timeframe])
    return CandleSeries(
        instrument=inst, timeframe=timeframe, candles=bars,
        source="RECONSTRUCTED_RESEARCH_ONLY")


def _brief(facts: dict) -> tuple[str, str | None, float | None, float | None, str]:
    statuses = [fact["status"] for fact in facts.values()]
    if all(x in {"AVAILABLE", "NOT_APPLICABLE"} for x in statuses):
        status = "FACTS_AVAILABLE"
    elif "INSUFFICIENT_HISTORY" in statuses:
        status = "INSUFFICIENT_HISTORY"
    elif "UNAVAILABLE" in statuses:
        status = "FACTS_PARTIAL"
    else:
        status = "FACTS_PARTIAL"
    structure = facts["confirmed_price_structure"]
    atr = facts["wilder_atr14"]
    volume = facts["relative_volume_prior20"]
    return (status,
            structure["value"]["structure"] if structure["value"] else None,
            atr["value"]["atr_pct"] if atr["value"] else None,
            volume["value"]["ratio_to_prior_20_median"] if volume["value"] else None,
            ";".join(name+":"+value["status"]+":"+str(value["reason"]) for name,value in facts.items()
                     if value["status"] not in {"AVAILABLE", "NOT_APPLICABLE"}) or "OK")


def run(archive: Path) -> dict:
    from scripts.g2_h1_duration_forensics import audit_bundle
    with zipfile.ZipFile(archive) as z:
        input_bytes = z.read(ENTRY)
        receipt = json.loads(z.read(RECEIPT))
    input_sha = hashlib.sha256(input_bytes).hexdigest()
    if receipt["input_sha256"] != input_sha:
        raise ValueError("source input and original receipt SHA mismatch")
    bundle = json.loads(input_bytes)
    audit = audit_bundle(bundle, receipt)
    if audit["total_counts"].get("false_from_duration_rule") != 933:
        raise ValueError("frozen archive no longer matches accepted 933 duration marks")
    cases = bundle["cases"]
    if len(cases) != 30 or len({c["case_id"] for c in cases}) != 30:
        raise ValueError("must be exactly original 30 distinct cases")
    results = []
    for case in cases:
        d1 = _series(case, "D1")
        h1 = _series(case, "H1")
        cutoff = case["cutoff_at"]
        # Always call the REAL unchanged strict code for D1 and try H1, too.
        d1_result = build_market_structure_facts(d1, t0=cutoff,
                                                  mode="RECONSTRUCTED_RESEARCH_ONLY")
        try:
            strict = build_market_structure_facts(h1, t0=cutoff,
                                                 mode="RECONSTRUCTED_RESEARCH_ONLY")
            strict_status = "RETURNED_"+_brief(strict["facts"])[0]
        except ValueError as error:
            strict_status = "REJECTED_"+str(error)
        h1_result = build_reconstructed_market_structure_facts(
            h1,t0=cutoff,timezone_assumption=ASSUMPTION,original_input_sha256=input_sha)
        d1_status, d1_structure, d1_atr, d1_vol, d1_reason = _brief(d1_result["facts"])
        h1_status, h1_structure, h1_atr, h1_vol, h1_reason = _brief(h1_result["facts"])
        if len(h1.candles) < 50:
            h1_reason = "H1 rows="+str(len(h1.candles))+" (<50); "+h1_reason
        if h1_result["original_completed_false"] > 0 and h1_status == "FACTS_AVAILABLE":
            # Allowed only as UNVERIFIED descriptive work, never Forecast.
            assert not h1_result["forecast_or_trading_authorized"]
        results.append({
            "case_id":case["case_id"],"symbol":case["instrument"]["symbol"],
            "secid":case["instrument"]["secid"],"t0":cutoff,
            "d1_status":d1_status,"d1_structure":d1_structure,"d1_atr_pct":d1_atr,
            "d1_volume_ratio":d1_vol,"d1_reason":d1_reason,
            "h1_status":h1_status,"h1_structure":h1_structure,"h1_atr_pct":h1_atr,
            "h1_volume_ratio":h1_vol,"h1_reason":h1_reason,
            "h1_original_false":h1_result["original_completed_false"],
            "h1_bars":len(h1.candles),"h1_strict_result":strict_status,
            "d1_input_sha256":d1_result["data_sha256"],
            "h1_input_sha256":h1_result["data_sha256"],
            "d1_full_result":d1_result,"h1_full_result":h1_result,
        })
    return {
        "schema":"G2_DIRECT_MODULE_RUN_V1_20261017",
        "module_version":VERSION,"source_file":"src/birzha/application/market_structure_facts.py",
        "cutoff_timezone_assumption":ASSUMPTION,
        "archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
        "input_sha256":input_sha,
        "source_completion_bit_recovered":False,
        "relabels_original_completed":False,
        "independent_first_receipt_attested":False,
        "forecast_admission":False,
        "duration_audit_false_marks":audit["total_counts"]["false_from_duration_rule"],
        "d1_facts_available":sum(r["d1_status"]=="FACTS_AVAILABLE" for r in results),
        "h1_facts_available":sum(r["h1_status"]=="FACTS_AVAILABLE" for r in results),
        "h1_history_insufficient":sum(r["h1_status"]=="INSUFFICIENT_HISTORY" for r in results),
        "records":results,
    }


def write_result(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir/"module_results_30_cases.json"
    table = out_dir/"module_results_30_cases.csv"
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                 indent=2, allow_nan=False), encoding="utf-8")
    with table.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for item in result["records"]:
            writer.writerow({name:item.get(name) for name in FIELDS})
    checksums = {
        "archive_sha256":result["archive_sha256"],
        "input_sha256":result["input_sha256"],
        output.name:hashlib.sha256(output.read_bytes()).hexdigest(),
        table.name:hashlib.sha256(table.read_bytes()).hexdigest(),
    }
    (out_dir/"SHA256.txt").write_text(
        "\n".join(f"{k}  {v}" for k,v in checksums.items())+"\n",encoding="utf-8"
    )
    print(json.dumps({"d1":result["d1_facts_available"],"h1":result["h1_facts_available"],
                       "h1_insufficient":result["h1_history_insufficient"],
                       "sha256":checksums},ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--out-dir",type=Path,required=True)
    args=parser.parse_args()
    write_result(run(args.archive),args.out_dir)


if __name__ == "__main__":
    main()
