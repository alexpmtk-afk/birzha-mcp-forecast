"""Execute EXACT Market Structure code on the fixed, owner-supplied G2 ZIP.

Read-only reconstructed descriptive experiment. Does NOT touch Forecast/Outcome,
MOEX, HOME, 2023-24 holdout, timestamps of receipt, or primary completion bits.
Run: python scripts/g2_market_structure_real_archive_runner.py --archive ZIP
       --output-dir DIR [--reference INDEPENDENT_20261016.json]
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import zipfile

from birzha.application.market_structure_facts import (
    ARCHIVED_SOURCE, VERSION, build_market_structure_facts,
    build_archived_h1_descriptive_facts,
)
from birzha.domain.market import Candle, CandleSeries, Instrument

INPUT_MEMBER = "allowed-real-run-v2/inputs.json"
RECEIPT_MEMBER = "allowed-real-run-v2/execution-receipt.json"
EXPECTED_INPUT_SHA = "dabb1eca56ae69267829b81f9b13318cfb6c142806c3b2229eafd24d684b0c03"
EXPECTED_ARCHIVE_SHA = "a80b2762d4a7af155af6863790dd50e220b1e49a61db9c330015fec654b13858"
MINSK_MOSCOW_2025_26 = timezone(timedelta(hours=3))
REPORT_SCHEMA = "G2_REAL_MARKET_STRUCTURE_REPLAY_V2_20261017"


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _timestamp(value: str) -> str:
    # The archived source is Moscow-local and *naive*. Make this explicit.
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MINSK_MOSCOW_2025_26)
    elif parsed.utcoffset() != timedelta(hours=3):
        raise ValueError("source timestamp timezone differs from explicit +03")
    return parsed.isoformat()


def _series(case: dict, timeframe: str) -> CandleSeries:
    data = dict(case["instrument"])
    data["data_capabilities"] = tuple(data["data_capabilities"])
    instrument = Instrument(**data)
    source = ARCHIVED_SOURCE if timeframe == "H1" else "G2_RECONSTRUCTED_D1_SOURCE"
    bars = tuple(
        Candle(
            **{**raw, "begin": _timestamp(raw["begin"]), "end": _timestamp(raw["end"]),
               "source": source,
               # Original archive lacks these fields. Never invent them.
               "available_at": None, "observed_at": None,
               "available_at_confidence": "UNKNOWN"}
        )
        for raw in case["series"][timeframe]
    )
    return CandleSeries(instrument=instrument, timeframe=timeframe,
                        candles=bars, source=source)


def _signature(result: dict) -> dict:
    facts = result["facts"]
    structure = facts["confirmed_price_structure"]
    atr = facts["wilder_atr14"]
    volume = facts["relative_volume_prior20"]
    return {
        "structure_status": structure["status"],
        "structure": (structure["value"] or {}).get("structure"),
        "wilder_status": atr["status"],
        "wilder_atr_price": (atr["value"] or {}).get("atr_price"),
        "wilder_atr_percent": (atr["value"] or {}).get("atr_pct"),
        "wilder_atr_ratio": (atr["value"] or {}).get("atr_ratio"),
        "atr_change_5_pct": (atr["value"] or {}).get("change_from_5_bars_ago_pct"),
        "volume_status": volume["status"],
        "volume_to_prior_20_median": (volume["value"] or {}).get("ratio_to_prior_20_median"),
    }


def _compare(old: dict, new: dict) -> list[str]:
    errors = []
    mapping = {
        "structure": ("structure", None),
        "structure_status": ("structure_status", None),
        "wilder_atr_price": ("wilder_atr14_price", 0.00000002),
        "wilder_atr_percent": ("wilder_atr14_pct", 0.000002),
        "atr_change_5_pct": ("atr_change_5_pct", 0.000002),
        "volume_status": ("volume_status", None),
        "volume_to_prior_20_median": ("volume_to_prior_20_median", 0.000002),
    }
    for key, (oldkey, tolerance) in mapping.items():
        prior = old.get(oldkey)
        now = new.get(key)
        if tolerance is None:
            if prior != now:
                errors.append(f"{key}:old={prior},new={now}")
        elif prior is None or now is None:
            if prior != now:
                errors.append(f"{key}:old={prior},new={now}")
        elif abs(float(prior) - float(now)) > tolerance:
            errors.append(f"{key}:old={prior},new={now}")
    return errors


def run(archive: Path, *, reference: Path | None = None) -> dict:
    content = archive.read_bytes()
    if _hash(content) != EXPECTED_ARCHIVE_SHA:
        raise ValueError("archival ZIP SHA not expected fixed input")
    with zipfile.ZipFile(archive) as z:
        raw = z.read(INPUT_MEMBER)
        receipt = json.loads(z.read(RECEIPT_MEMBER))
    if _hash(raw) != EXPECTED_INPUT_SHA or receipt.get("input_sha256") != EXPECTED_INPUT_SHA:
        raise ValueError("frozen input or receipt hash mismatch")
    bundle = json.loads(raw)
    if (bundle.get("schema") != "G2_PRICE_REPLAY_INPUT_BUNDLE_V1" or
            bundle.get("scope") != "RECONSTRUCTED_HISTORICAL_PRICE_ENGINE_ONLY"):
        raise ValueError("unexpected input scope")
    if len(bundle["cases"]) != 30 or len({c["case_id"] for c in bundle["cases"]}) != 30:
        raise ValueError("expected 30 unique fixed contexts")
    receipt_counts = {(r["case_id"], r["timeframe"]): r["incomplete_duration_marked"]
                      for r in receipt["duration_normalization"]}
    if len(receipt_counts) != len(receipt["duration_normalization"]):
        raise ValueError("duplicate receipt identities")

    ref_records = {}
    if reference is not None:
        original = json.loads(reference.read_text(encoding="utf-8"))
        if original.get("input_bundle_sha256") != EXPECTED_INPUT_SHA:
            raise ValueError("independent reference input differs")
        ref_records = {(r["case_id"], r["timeframe"]): r for r in original["records"]}
        if len(ref_records) != 60:
            raise ValueError("expect 30 D1 and 30 H1 prior rows")

    rows = []
    mismatches = []
    expected_cases = []
    for case in bundle["cases"]:
        row = {
            "case_id": case["case_id"], "symbol": case["instrument"]["symbol"],
            "secid": case["instrument"]["secid"], "cutoff_at": case["cutoff_at"],
        }
        for tf in ("D1", "H1"):
            series = _series(case, tf)
            flags = sum(not b.completed for b in series.candles)
            if receipt_counts.get((case["case_id"], tf)) != flags:
                raise ValueError("duration count differs from frozen receipt")
            part = tf.lower()
            row[f"{part}_input_bars"] = len(series.candles)
            row[f"{part}_duration_flags"] = flags
            row[f"{part}_source_sha256"] = _hash(json.dumps(case["series"][tf], sort_keys=True,
                                             ensure_ascii=False, separators=(",", ":")).encode())
            row[f"{part}_strict"] = "UNSET"
            row[f"{part}_research"] = "UNSET"
            row[f"{part}_research_reason"] = ""
            # This executes the actual strict entrypoint, not a duplicated formula.
            try:
                strict = build_market_structure_facts(series, t0=case["cutoff_at"])
                row[f"{part}_strict"] = "PASS"
                if tf == "D1":
                    descriptive = strict
                else:
                    # Run also on strict-admissible H1 to test exact shared parity.
                    descriptive = build_archived_h1_descriptive_facts(series, t0=case["cutoff_at"])
                    if strict["facts"] != descriptive["facts"]:
                        raise AssertionError("shared H1 computation differs between entrypoints")
            except ValueError as exc:
                row[f"{part}_strict"] = "REJECTED:" + str(exc)
                if tf == "D1":
                    raise
                descriptive = build_archived_h1_descriptive_facts(series, t0=case["cutoff_at"])
            if descriptive["schema"] != VERSION:
                raise AssertionError("wrong computation version")
            signature = _signature(descriptive)
            row[f"{part}_research"] = "FEATURES_CALCULATED" if len(series.candles) >= 50 else "INSUFFICIENT_50_HISTORICAL_BARS"
            if len(series.candles) < 50:
                row[f"{part}_research_reason"] = f"need_50_have_{len(series.candles)}_no_substitution"
            elif tf == "H1":
                row[f"{part}_research_reason"] = (
                    "ORIGINAL_COMPLETION_UNKNOWN_FOR_DURATION_FLAGS"
                    if flags else "RECONSTRUCTED_NO_INDEPENDENT_FIRST_RECEIPT"
                )
            else:
                row[f"{part}_research_reason"] = "RECONSTRUCTED_NO_INDEPENDENT_FIRST_RECEIPT"
            for k, v in signature.items():
                row[f"{part}_{k}"] = v
            row[f"{part}_computed_sha256"] = descriptive["data_sha256"]
            if tf == "H1":
                assert descriptive["duration_flagged_bars"] == flags
                assert descriptive["archived_completed_flags_preserved"] is True
                assert descriptive["forecast_or_trading_authorized"] is False
                assert descriptive["strict_pit_admitted"] is False
            if (case["case_id"], tf) in ref_records:
                old = ref_records[(case["case_id"], tf)]
                if old["status"] == "RECONSTRUCTED_FEATURES":
                    mismatches += [{"case_id": case["case_id"], "timeframe": tf, "mismatch": e}
                                   for e in _compare(old, signature)]
                else:
                    # An old H1 "INCOMPLETE_BAR" is an *eligibility* disagreement,
                    # not a forecast or formula discrepancy.
                    if tf == "H1" and len(series.candles) >= 50:
                        expected_cases.append(case["case_id"])
        rows.append(row)
    if mismatches:
        raise AssertionError(f"independent formula mismatches: {mismatches[:10]}")
    row_counts = {
        "d1_research_calculated": sum(r["d1_research"] == "FEATURES_CALCULATED" for r in rows),
        "h1_research_calculated": sum(r["h1_research"] == "FEATURES_CALCULATED" for r in rows),
        "h1_strict_pass": sum(r["h1_strict"] == "PASS" for r in rows),
        "h1_insufficient": sum(r["h1_research"] != "FEATURES_CALCULATED" for r in rows),
        "h1_strict_rejected_with_50_plus": sum(
            r["h1_strict"].startswith("REJECTED") and r["h1_input_bars"] >= 50 for r in rows
        ),
        "raw_archived_h1_duration_flags": sum(r["h1_duration_flags"] for r in rows),
    }
    if row_counts != {
        "d1_research_calculated": 30, "h1_research_calculated": 23,
        "h1_strict_pass": 5, "h1_insufficient": 7,
        "h1_strict_rejected_with_50_plus": 23, "raw_archived_h1_duration_flags": 933,
    }:
        # Two GOLD/BR cases with zero/short history may pass validation but have
        # no features. Guard exact status instead of treating a pass as eligibility.
        raise AssertionError("fixed-window counts unexpectedly changed: " + repr(row_counts))
    return {
        "schema": REPORT_SCHEMA,
        "code_feature_version": VERSION,
        "timezone_assumption": "Europe/Moscow, fixed UTC+03:00 for 2025-2026 naive archive fields",
        "timezone_not_independently_attested": True,
        "source_archive_sha256": _hash(content),
        "source_inputs_sha256": _hash(raw),
        "independent_reference_sha256": _hash(reference.read_bytes()) if reference else None,
        "prospective_forecast": False,
        "primary_completed_certified": False,
        "independent_first_receipt_attested": False,
        "original_prices_or_flags_mutated": False,
        "reference_formulas_mismatch_count": len(mismatches),
        "reference_old_exclusions_explained": len(expected_cases),
        "summary": row_counts,
        "rows": rows,
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--archive", type=Path, required=True)
    cli.add_argument("--output-dir", type=Path, required=True)
    cli.add_argument("--reference", type=Path)
    args = cli.parse_args()
    result = run(args.archive, reference=args.reference)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "market_structure_run_30_cases.json"
    output.write_text(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    csvpath = args.output_dir / "market_structure_run_30_cases.csv"
    with csvpath.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(result["rows"][0]), extrasaction="raise")
        writer.writeheader()
        writer.writerows(result["rows"])
    print(json.dumps({
        "status": "PASS",
        "summary": result["summary"],
        "feature_version": result["code_feature_version"],
        "output_sha256": _hash(output.read_bytes()),
        "csv_sha256": _hash(csvpath.read_bytes()),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
