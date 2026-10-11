"""Read-only descriptive scorecard for the isolated prospective capture ledger.

This compares frozen directional forecasts with a fixed always-UP reference on
the same completed, hash-consistent outcome rows. It does not write to a ledger.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any


LEDGER_VERSION = "G2_PROSPECTIVE_CAPTURE_PILOT_V1"
SCORECARD_VERSION = "G2_PROSPECTIVE_ALWAYS_UP_SCORECARD_V1"
HORIZONS = (5, 10, 20)
RECORD_VERSIONS = {
    "FORECAST_RECORD_V1_PROTOCOL_08",
    "FORECAST_RECORD_V2_BASELINE_EVIDENCE",
    "FORECAST_RECORD_V3_LEVEL_EVIDENCE",
}


class ScorecardRefused(ValueError):
    """The supplied ledger is incomplete, inconsistent, or not this pilot."""


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ScorecardRefused(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ScorecardRefused(f"{label} must be a JSON object")
    return value


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ScorecardRefused(f"{label} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScorecardRefused(f"{label} must be a timezone-aware timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScorecardRefused(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScorecardRefused(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ScorecardRefused(f"{label} must be a finite number")
    return number


def _read_ledger(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not path.is_file() or path.suffix != ".sqlite3":
        raise ScorecardRefused("select an existing .sqlite3 prospective pilot ledger")
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ScorecardRefused("the pilot ledger could not be opened read-only") from exc
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not {"pilot_meta", "captures", "outcomes"}.issubset(tables):
            raise ScorecardRefused("the file is not a prospective pilot ledger")
        versions = [row[0] for row in connection.execute("SELECT version FROM pilot_meta")]
        if versions != [LEDGER_VERSION]:
            raise ScorecardRefused("unsupported prospective pilot ledger version")
        captures = connection.execute(
            "SELECT forecast_id,forecast_json,forecast_sha,input_blob,input_sha,receipt_json,receipt_sha "
            "FROM captures ORDER BY forecast_id"
        ).fetchall()
        outcomes = connection.execute(
            "SELECT forecast_id,horizon,payload_json,payload_sha,input_blob,input_sha "
            "FROM outcomes ORDER BY forecast_id,horizon"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ScorecardRefused("the prospective pilot ledger could not be read") from exc
    finally:
        connection.close()

    forecast_rows: list[dict[str, Any]] = []
    for fid, raw, digest, source, source_digest, receipt_raw, receipt_digest in captures:
        if _sha(raw.encode("utf-8")) != digest or _sha(source) != source_digest:
            raise ScorecardRefused(f"capture evidence hash mismatch: {fid}")
        if _sha(receipt_raw.encode("utf-8")) != receipt_digest:
            raise ScorecardRefused(f"capture receipt hash mismatch: {fid}")
        forecast = _json_object(raw, f"forecast {fid}")
        receipt = _json_object(receipt_raw, f"receipt {fid}")
        if forecast.get("forecast_id") != fid or receipt.get("forecast_id") != fid:
            raise ScorecardRefused(f"forecast identity mismatch: {fid}")
        if receipt.get("forecast_sha256") != digest or receipt.get("source_sha256") != source_digest:
            raise ScorecardRefused(f"capture receipt is not bound to its evidence: {fid}")
        forecast_rows.append({"forecast": forecast, "receipt": receipt})

    outcome_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for fid, horizon, raw, digest, source, source_digest in outcomes:
        if _sha(raw.encode("utf-8")) != digest or _sha(source) != source_digest:
            raise ScorecardRefused(f"outcome evidence hash mismatch: {fid}/{horizon}")
        outcome = _json_object(raw, f"outcome {fid}/{horizon}")
        identity = (fid, int(horizon))
        if identity in seen:
            raise ScorecardRefused(f"duplicate outcome identity: {fid}/{horizon}")
        seen.add(identity)
        if outcome.get("future_source_sha256") != source_digest:
            raise ScorecardRefused(f"outcome is not bound to its original bytes: {fid}/{horizon}")
        outcome_rows.append({"forecast_id": fid, "horizon": int(horizon), "outcome": outcome})
    return forecast_rows, outcome_rows


def _forecast_horizons(forecast: dict[str, Any]) -> dict[int, dict[str, Any]]:
    if forecast.get("record_version") not in RECORD_VERSIONS:
        raise ScorecardRefused(f"unsupported Forecast Record version: {forecast.get('record_version')}")
    horizons = forecast.get("horizons")
    if not isinstance(horizons, list):
        raise ScorecardRefused("Forecast Record has no horizon list")
    by_session: dict[int, dict[str, Any]] = {}
    for item in horizons:
        if not isinstance(item, dict) or isinstance(item.get("sessions"), bool):
            raise ScorecardRefused("invalid forecast horizon entry")
        sessions = item.get("sessions")
        if not isinstance(sessions, int) or sessions in by_session:
            raise ScorecardRefused("duplicate or invalid forecast horizon")
        by_session[sessions] = item
    if set(by_session) != set(HORIZONS):
        raise ScorecardRefused("scorecard requires exactly 5, 10, and 20 session forecasts")
    return by_session


def _decision(forecast: dict[str, Any], horizon: dict[str, Any]) -> str:
    status = forecast.get("decision_status")
    if status not in {None, "ABSTAIN", "BASELINE_NEUTRAL", "BASELINE_DIRECTIONAL_ESTIMATE"}:
        raise ScorecardRefused("unknown decision status")
    if status == "ABSTAIN":
        return "ABSTAIN"
    direction = horizon.get("direction")
    if status == "BASELINE_NEUTRAL":
        if direction != "NEUTRAL":
            raise ScorecardRefused("neutral decision conflicts with horizon direction")
        return "NEUTRAL"
    if direction in {"UP", "DOWN"}:
        if status == "BASELINE_DIRECTIONAL_ESTIMATE":
            return direction
        if status is None:
            return direction
    if status == "BASELINE_DIRECTIONAL_ESTIMATE":
        raise ScorecardRefused("directional decision has no directional horizon")
    if direction == "NEUTRAL":
        return "NEUTRAL"
    raise ScorecardRefused("unknown forecast direction")


def _check_outcome(
    forecast: dict[str, Any], receipt: dict[str, Any], horizon: int, outcome: dict[str, Any]
) -> float:
    fid = forecast["forecast_id"]
    if outcome.get("forecast_id") != fid or outcome.get("horizon_sessions") != horizon:
        raise ScorecardRefused(f"outcome does not match forecast and horizon: {fid}/{horizon}")
    if outcome.get("status") != "OBSERVED" or outcome.get("pilot_only") is not True:
        raise ScorecardRefused(f"outcome is not a completed pilot observation: {fid}/{horizon}")
    if outcome.get("market") != forecast.get("symbol") or outcome.get("secid") != forecast.get("secid"):
        raise ScorecardRefused(f"market or exact SECID mismatch: {fid}/{horizon}")
    if receipt.get("symbol") != forecast.get("symbol") or receipt.get("secid") != forecast.get("secid"):
        raise ScorecardRefused(f"capture receipt market or SECID mismatch: {fid}")

    t0 = _time(forecast.get("created_at_t0"), f"forecast T0 {fid}")
    captured = _time(receipt.get("captured_at"), f"capture time {fid}")
    receipt_t0 = _time(receipt.get("decision_t0"), f"receipt T0 {fid}")
    source_seen = _time(receipt.get("source_observed_at"), f"capture source time {fid}")
    latest_event = _time(receipt.get("latest_completed_event_end"), f"capture event time {fid}")
    observed = _time(outcome.get("source_observed_at"), f"outcome source time {fid}/{horizon}")
    written = _time(outcome.get("outcome_written_at"), f"outcome write time {fid}/{horizon}")
    target_end = _time(outcome.get("target_session_end"), f"target close time {fid}/{horizon}")
    if (
        receipt_t0 != t0 or latest_event > source_seen or source_seen > t0
        or captured < source_seen or captured < t0 or target_end <= t0
        or target_end > observed or observed <= captured or written < observed
    ):
        raise ScorecardRefused(f"outcome time sequence is invalid: {fid}/{horizon}")
    try:
        target_day = date.fromisoformat(outcome.get("target_session"))
    except (TypeError, ValueError) as exc:
        raise ScorecardRefused(f"invalid outcome target session: {fid}/{horizon}") from exc
    if target_day <= t0.date():
        raise ScorecardRefused(f"outcome is not after forecast date: {fid}/{horizon}")

    reference = _finite_number(forecast.get("reference_price"), f"forecast reference price {fid}")
    target = _finite_number(outcome.get("target_close"), f"target close {fid}/{horizon}")
    recorded_reference = _finite_number(outcome.get("reference_price"), f"outcome reference price {fid}/{horizon}")
    actual_return = _finite_number(outcome.get("actual_return_pct"), f"actual return {fid}/{horizon}")
    if reference <= 0 or target <= 0 or recorded_reference != reference:
        raise ScorecardRefused(f"outcome price identity mismatch: {fid}/{horizon}")
    calculated_return = round((target / reference - 1.0) * 100.0, 8)
    if actual_return != calculated_return:
        raise ScorecardRefused(f"recorded horizon return is inconsistent: {fid}/{horizon}")
    horizon_row = _forecast_horizons(forecast)[horizon]
    direction = _decision(forecast, horizon_row)
    expected_hit = None if direction in {"NEUTRAL", "ABSTAIN"} else (
        actual_return > 0 if direction == "UP" else actual_return < 0
    )
    if outcome.get("direction_hit") is not expected_hit:
        raise ScorecardRefused(f"recorded direction result is inconsistent: {fid}/{horizon}")
    return actual_return


def build_scorecard(path: Path) -> dict[str, Any]:
    captures, outcome_rows = _read_ledger(path)
    forecasts: dict[str, dict[str, Any]] = {}
    expected: dict[tuple[str, int], dict[str, Any]] = {}
    groups: dict[tuple[str, str, int], dict[str, Any]] = defaultdict(
        lambda: {"captured": 0, "up": 0, "down": 0, "neutral": 0, "abstain": 0,
                 "matured": 0, "candidate_correct": 0, "always_up_correct": 0}
    )
    for item in captures:
        forecast, receipt = item["forecast"], item["receipt"]
        fid = forecast["forecast_id"]
        if fid in forecasts:
            raise ScorecardRefused(f"duplicate forecast identity: {fid}")
        if not forecast.get("symbol") or not forecast.get("secid"):
            raise ScorecardRefused(f"missing market or exact SECID: {fid}")
        forecasts[fid] = item
        for horizon, row in _forecast_horizons(forecast).items():
            decision = _decision(forecast, row)
            key = (str(forecast["symbol"]), str(forecast["secid"]), horizon)
            bucket = groups[key]
            bucket["captured"] += 1
            bucket[{"UP": "up", "DOWN": "down", "NEUTRAL": "neutral", "ABSTAIN": "abstain"}[decision]] += 1
            expected[(fid, horizon)] = {"item": item, "decision": decision, "group": key}

    for entry in outcome_rows:
        fid, horizon, outcome = entry["forecast_id"], entry["horizon"], entry["outcome"]
        linked = expected.get((fid, horizon))
        if linked is None:
            raise ScorecardRefused(f"outcome has no matching frozen forecast: {fid}/{horizon}")
        forecast, receipt = linked["item"]["forecast"], linked["item"]["receipt"]
        actual_return = _check_outcome(forecast, receipt, horizon, outcome)
        bucket = groups[linked["group"]]
        bucket["matured"] += 1
        direction = linked["decision"]
        if direction in {"UP", "DOWN"}:
            model_correct = actual_return > 0 if direction == "UP" else actual_return < 0
            always_up_correct = actual_return > 0
            bucket["candidate_correct"] += int(model_correct)
            bucket["always_up_correct"] += int(always_up_correct)

    rows: list[dict[str, Any]] = []
    for (market, secid, horizon), value in sorted(groups.items()):
        directional = value["up"] + value["down"]
        completed_ids = {(row["forecast_id"], row["horizon"]) for row in outcome_rows}
        scored = sum(
            1 for (fid, h), entry in expected.items()
            if h == horizon and entry["group"] == (market, secid, horizon)
            and entry["decision"] in {"UP", "DOWN"} and (fid, h) in completed_ids
        )
        pending = value["captured"] - value["matured"]
        candidate_accuracy = value["candidate_correct"] / scored if scored else None
        baseline_accuracy = value["always_up_correct"] / scored if scored else None
        rows.append({
            "market": market,
            "secid": secid,
            "horizon_sessions": horizon,
            "forecasts_frozen": value["captured"],
            "outcomes_completed": value["matured"],
            "outcomes_pending": pending,
            "directional_forecasts": directional,
            "explicit_abstentions": value["abstain"],
            "neutral_forecasts": value["neutral"],
            "directional_forecasts_scored": scored,
            "candidate_correct": value["candidate_correct"],
            "always_up_correct_same_rows": value["always_up_correct"],
            "candidate_accuracy": candidate_accuracy,
            "always_up_accuracy_same_rows": baseline_accuracy,
            "accuracy_difference_percentage_points": (
                (candidate_accuracy - baseline_accuracy) * 100.0
                if candidate_accuracy is not None and baseline_accuracy is not None else None
            ),
            "directional_coverage_of_frozen_forecasts": directional / value["captured"] if value["captured"] else None,
        })
    return {
        "scorecard_version": SCORECARD_VERSION,
        "ledger_version": LEDGER_VERSION,
        "read_only": True,
        "comparison": "fixed always-UP reference on the same completed outcomes as directional forecasts",
        "metric": "horizon-end close direction; a flat return is incorrect for either directional call",
        "groups": rows,
        "limits": [
            "This reports descriptive counts and accuracy only; it does not select or tune a model.",
            "The prospective pilot ledger is local evidence; hashes do not authenticate an outside source, clock, or calendar.",
            "This is not first-touch outcome scoring, profitability, independent out-of-sample proof, or release acceptance.",
            "Overlapping forecast horizons and repeated origins are dependent; no independent-sample confidence claim is made.",
            "Pending outcomes remain pending and are never filled with zero or inferred from a later forecast.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Сравнить сохранённые направления прогноза с неизменным ориентиром «всегда рост»; только чтение."
    )
    parser.add_argument("--ledger", required=True, type=Path, help="явный путь к файлу отдельного опытного журнала .sqlite3")
    parser.add_argument("--output", type=Path, help="необязательный путь для сводки JSON; без него печать в окно")
    args = parser.parse_args(argv)
    try:
        report = build_scorecard(args.ledger)
    except ScorecardRefused as exc:
        print(f"Сводка не построена: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
        return 0
    try:
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    except OSError:
        print("Сводка не записана: не удалось создать выходной файл.", file=sys.stderr)
        return 2
    print(f"Сводка сохранена: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
