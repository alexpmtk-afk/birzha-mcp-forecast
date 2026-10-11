"""Read-only descriptive scorecard for the isolated prospective capture ledger.

This compares frozen directional forecasts with a fixed always-UP reference on
the same completed, hash-consistent outcome rows. It does not write to a ledger.
"""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zoneinfo import ZoneInfo


LEDGER_VERSION = "G2_PROSPECTIVE_CAPTURE_PILOT_V1"
DUCKDB_LEDGER_VERSION = "G2_ATOMIC_SINGLE_DUCKDB_STAGING_V2"
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


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


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


def _check_capture_times(forecast: dict[str, Any], receipt: dict[str, Any], fid: str) -> None:
    t0 = _time(forecast.get("created_at_t0"), f"forecast T0 {fid}")
    receipt_t0 = _time(receipt.get("decision_t0"), f"receipt T0 {fid}")
    source_seen = _time(receipt.get("source_observed_at"), f"capture source time {fid}")
    latest_event = _time(receipt.get("latest_completed_event_end"), f"capture event time {fid}")
    captured = _time(receipt.get("captured_at"), f"capture time {fid}")
    if receipt_t0 != t0 or latest_event > source_seen or source_seen > t0 or captured < max(source_seen, t0):
        raise ScorecardRefused(f"capture time sequence is invalid: {fid}")


def _read_sqlite_pilot(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
        _check_capture_times(forecast, receipt, str(fid))
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


def _read_duckdb_staging(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the isolated single-file staging journal without opening it for writes."""
    try:
        import duckdb
    except ImportError as exc:
        raise ScorecardRefused("DuckDB support is unavailable in this Python environment") from exc
    try:
        connection = duckdb.connect(str(path.resolve()), read_only=True)
    except Exception as exc:
        raise ScorecardRefused("the DuckDB journal could not be opened read-only") from exc
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()}
        required = {
            "g2_atomic_meta", "forecast_records", "outcome_records",
            "g2_atomic_captures", "g2_atomic_futures",
        }
        if not required.issubset(tables):
            raise ScorecardRefused("the DuckDB file is not a G2 single-file staging journal")
        versions = [row[0] for row in connection.execute("SELECT version FROM g2_atomic_meta").fetchall()]
        if versions != [DUCKDB_LEDGER_VERSION]:
            raise ScorecardRefused("unsupported G2 DuckDB staging journal version")
        forecasts = connection.execute(
            "SELECT forecast_id,payload_hash,payload_json,symbol,secid,created_at_t0 "
            "FROM forecast_records ORDER BY forecast_id"
        ).fetchall()
        captures = connection.execute(
            "SELECT forecast_id,forecast_sha,source_blob,source_sha,receipt_json,receipt_sha "
            "FROM g2_atomic_captures ORDER BY forecast_id"
        ).fetchall()
        outcomes = connection.execute(
            "SELECT outcome_id,forecast_id,horizon_sessions,payload_hash,payload_json "
            "FROM outcome_records ORDER BY forecast_id,horizon_sessions"
        ).fetchall()
        futures = connection.execute(
            "SELECT forecast_id,horizon,outcome_id,source_blob,source_sha,evidence_json,evidence_sha "
            "FROM g2_atomic_futures ORDER BY forecast_id,horizon"
        ).fetchall()
    except ScorecardRefused:
        raise
    except Exception as exc:
        raise ScorecardRefused("the G2 DuckDB journal could not be read") from exc
    finally:
        connection.close()

    forecast_by_id = {str(row[0]): row for row in forecasts}
    capture_by_id = {str(row[0]): row for row in captures}
    if len(forecast_by_id) != len(forecasts) or set(forecast_by_id) != set(capture_by_id):
        raise ScorecardRefused("DuckDB forecast and source receipt inventories differ")
    forecast_rows: list[dict[str, Any]] = []
    for fid, row in forecast_by_id.items():
        _, digest, raw, symbol, secid, t0 = row
        _, receipt_digest, source, source_digest, receipt_raw, stored_receipt_digest = capture_by_id[fid]
        raw = str(raw)
        source = bytes(source)
        receipt_raw = str(receipt_raw)
        if digest != receipt_digest or _sha(raw.encode("utf-8")) != digest:
            raise ScorecardRefused(f"canonical forecast hash mismatch: {fid}")
        if _sha(source) != source_digest:
            raise ScorecardRefused(f"source bytes hash mismatch: {fid}")
        if _sha(receipt_raw.encode("utf-8")) != stored_receipt_digest:
            raise ScorecardRefused(f"capture receipt hash mismatch: {fid}")
        forecast = _json_object(raw, f"forecast {fid}")
        receipt = _json_object(receipt_raw, f"receipt {fid}")
        if (
            forecast.get("forecast_id") != fid or forecast.get("symbol") != symbol
            or forecast.get("secid") != secid or forecast.get("created_at_t0") != t0
            or receipt.get("forecast_id") != fid or receipt.get("forecast_sha256") != digest
            or receipt.get("source_sha256") != source_digest or receipt.get("secid") != secid
            or receipt.get("staging_only") is not True
        ):
            raise ScorecardRefused(f"forecast and source receipt are not bound: {fid}")
        _check_capture_times(forecast, receipt, fid)
        manifest_b64 = receipt.get("original_source_manifest_b64")
        manifest_sha = receipt.get("original_source_manifest_sha256")
        if manifest_b64 is not None or manifest_sha is not None:
            if not isinstance(manifest_b64, str) or not isinstance(manifest_sha, str):
                raise ScorecardRefused(f"incomplete original source manifest receipt: {fid}")
            try:
                manifest_bytes = base64.b64decode(manifest_b64, validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise ScorecardRefused(f"invalid original source manifest receipt: {fid}") from exc
            if _sha(manifest_bytes) != manifest_sha:
                raise ScorecardRefused(f"original source manifest hash mismatch: {fid}")
        forecast_rows.append({"forecast": forecast, "receipt": receipt})

    outcome_by_id = {str(row[0]): row for row in outcomes}
    future_by_key = {(str(row[0]), int(row[1])): row for row in futures}
    future_outcome_ids = {str(row[2]) for row in futures}
    if (
        len(outcome_by_id) != len(outcomes) or len(future_by_key) != len(futures)
        or len(future_outcome_ids) != len(futures)
    ):
        raise ScorecardRefused("duplicate canonical outcome or future evidence identity")
    if future_outcome_ids != set(outcome_by_id):
        raise ScorecardRefused("canonical outcome and original future evidence inventories differ")

    outcome_rows: list[dict[str, Any]] = []
    for key, future in future_by_key.items():
        fid, horizon, outcome_id, source, source_digest, evidence_raw, evidence_digest = future
        canonical = outcome_by_id[str(outcome_id)]
        _, canonical_fid, canonical_horizon, payload_digest, payload_raw = canonical
        source = bytes(source)
        evidence_raw = str(evidence_raw)
        payload_raw = str(payload_raw)
        if _sha(source) != source_digest or _sha(evidence_raw.encode("utf-8")) != evidence_digest:
            raise ScorecardRefused(f"future evidence hash mismatch: {fid}/{horizon}")
        if _sha(payload_raw.encode("utf-8")) != payload_digest:
            raise ScorecardRefused(f"canonical outcome hash mismatch: {fid}/{horizon}")
        if (canonical_fid, int(canonical_horizon)) != key:
            raise ScorecardRefused(f"canonical outcome identity mismatch: {fid}/{horizon}")
        evidence = _json_object(evidence_raw, f"future evidence {fid}/{horizon}")
        canonical_outcome = _json_object(payload_raw, f"canonical outcome {fid}/{horizon}")
        future_input = evidence.get("future_input_evidence")
        if not isinstance(future_input, dict):
            raise ScorecardRefused(f"future input evidence is missing: {fid}/{horizon}")
        if _sha(_canonical_json(future_input)) != evidence.get("future_input_sha256"):
            raise ScorecardRefused(f"future input evidence hash mismatch: {fid}/{horizon}")
        session_dates = future_input.get("session_dates")
        calendar_dates = future_input.get("expected_calendar_dates")
        completion_times = future_input.get("candle_completed_at")
        closes = future_input.get("candle_close")
        if not all(isinstance(items, list) and len(items) == int(horizon)
                   for items in (session_dates, calendar_dates, completion_times, closes)):
            raise ScorecardRefused(f"future input window is incomplete: {fid}/{horizon}")
        if not all(isinstance(item, str) for item in session_dates):
            raise ScorecardRefused(f"future session dates are invalid: {fid}/{horizon}")
        try:
            parsed_days = tuple(date.fromisoformat(item) for item in session_dates)
            completed_at = tuple(_time(item, f"future candle completion {fid}/{horizon}") for item in completion_times)
            source_seen_at = _time(evidence.get("source_observed_at"), f"future source time {fid}/{horizon}")
        except (TypeError, ValueError) as exc:
            raise ScorecardRefused(f"future session chronology is invalid: {fid}/{horizon}") from exc
        if (
            any(day.isoformat() != raw for day, raw in zip(parsed_days, session_dates))
            or parsed_days != tuple(sorted(set(parsed_days)))
            or any(left >= right for left, right in zip(completed_at, completed_at[1:]))
            or any(end > source_seen_at or (end.astimezone(ZoneInfo("Europe/Moscow")).date() - day).days not in (0, 1)
                   for day, end in zip(parsed_days, completed_at))
            or any(_finite_number(value, f"future close {fid}/{horizon}") <= 0 for value in closes)
        ):
            raise ScorecardRefused(f"future session chronology or close is invalid: {fid}/{horizon}")
        if (
            session_dates != calendar_dates
            or evidence.get("calendar_sha256") != _sha(_canonical_json(calendar_dates))
            or (session_dates[-1], completion_times[-1], closes[-1]) != (
                evidence.get("target_session"), evidence.get("target_session_end"), evidence.get("target_close")
            )
            or future_input.get("source_observed_at") != evidence.get("source_observed_at")
            or future_input.get("source_origin") != "LIVE_COMPLETED_D1"
            or future_input.get("calendar_origin") != "VERIFIED_EXCHANGE_SESSION_CALENDAR"
        ):
            raise ScorecardRefused(f"future input window does not match its outcome: {fid}/{horizon}")
        if (
            evidence.get("forecast_id") != fid or evidence.get("horizon_sessions") != int(horizon)
            or evidence.get("future_source_sha256") != source_digest
            or future_input.get("source_sha256") != source_digest
            or future_input.get("secid") != evidence.get("secid")
            or future_input.get("market") != evidence.get("market")
        ):
            raise ScorecardRefused(f"future evidence is not bound to original bytes: {fid}/{horizon}")
        expected_outcome_id = "out_" + hashlib.sha256(f"{fid}:{int(horizon)}".encode()).hexdigest()[:24]
        if str(outcome_id) != expected_outcome_id:
            raise ScorecardRefused(f"canonical outcome identifier mismatch: {fid}/{horizon}")
        for field, value in {
            "outcome_id": str(outcome_id), "forecast_id": str(fid), "symbol": evidence.get("market"),
            "secid": evidence.get("secid"), "horizon_sessions": int(horizon),
            "reference_price": evidence.get("reference_price"),
            "target_session_end": evidence.get("target_session_end"),
            "target_close": evidence.get("target_close"),
            "actual_return_pct": round(
                _finite_number(evidence.get("actual_return_pct"), f"future return {fid}/{horizon}"), 6
            ),
            "direction_hit": evidence.get("direction_hit"), "status": "OBSERVED",
        }.items():
            if canonical_outcome.get(field) != value:
                raise ScorecardRefused(f"canonical outcome differs from original future: {fid}/{horizon}")
        normalized = {
            "forecast_id": str(fid), "horizon_sessions": int(horizon),
            "market": evidence.get("market"), "secid": evidence.get("secid"),
            "status": "OBSERVED", "target_session": evidence.get("target_session"),
            "target_session_end": evidence.get("target_session_end"),
            "target_close": evidence.get("target_close"),
            "reference_price": evidence.get("reference_price"),
            "actual_return_pct": evidence.get("actual_return_pct"),
            "direction_hit": evidence.get("direction_hit"),
            "source_observed_at": evidence.get("source_observed_at"),
            "capture_at": evidence.get("capture_at"),
            "outcome_written_at": evidence.get("outcome_written_at"),
            "future_source_sha256": source_digest, "pilot_only": True,
        }
        outcome_rows.append({"forecast_id": str(fid), "horizon": int(horizon), "outcome": normalized})
    return forecast_rows, outcome_rows


def _read_ledger(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not path.is_file():
        raise ScorecardRefused("select an existing isolated prospective journal file")
    if path.suffix == ".sqlite3":
        return _read_sqlite_pilot(path)
    if path.suffix == ".duckdb":
        return _read_duckdb_staging(path)
    raise ScorecardRefused("select an existing .sqlite3 or .duckdb prospective journal")


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
    direction = horizon.get("direction")
    reasons = forecast.get("abstention_reasons", [])
    if status == "ABSTAIN":
        if direction != "NEUTRAL" or not isinstance(reasons, list) or not reasons:
            raise ScorecardRefused("abstention must have neutral direction and explicit reasons")
        return "ABSTAIN"
    if status == "BASELINE_NEUTRAL":
        if direction != "NEUTRAL" or reasons:
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
    if (receipt.get("symbol") not in (None, forecast.get("symbol"))
            or receipt.get("secid") != forecast.get("secid")):
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
        "ledger_version": LEDGER_VERSION if path.suffix == ".sqlite3" else DUCKDB_LEDGER_VERSION,
        "storage_format": "sqlite3 pilot" if path.suffix == ".sqlite3" else "DuckDB staging journal",
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
    parser.add_argument("--ledger", required=True, type=Path, help="явный путь к изолированному опытному журналу .sqlite3 или .duckdb")
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

