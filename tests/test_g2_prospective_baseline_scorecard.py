from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.g2_prospective_baseline_scorecard import (
    LEDGER_VERSION,
    ScorecardRefused,
    build_scorecard,
)


def _encoded(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _forecast(fid: str, direction: str) -> dict[str, object]:
    return {
        "record_version": "FORECAST_RECORD_V1_PROTOCOL_08",
        "forecast_id": fid,
        "symbol": "SBER",
        "secid": "SBER",
        "created_at_t0": "2026-10-12T10:00:00Z",
        "reference_price": 100.0,
        "horizons": [
            {"sessions": horizon, "direction": direction}
            for horizon in (5, 10, 20)
        ],
    }


def _make_ledger(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE pilot_meta(version TEXT PRIMARY KEY);
        CREATE TABLE captures(
            forecast_id TEXT PRIMARY KEY, forecast_json TEXT NOT NULL,
            forecast_sha TEXT NOT NULL, input_blob BLOB NOT NULL,
            input_sha TEXT NOT NULL, receipt_json TEXT NOT NULL,
            receipt_sha TEXT NOT NULL, captured_at TEXT NOT NULL
        );
        CREATE TABLE outcomes(
            forecast_id TEXT NOT NULL, horizon INTEGER NOT NULL,
            payload_json TEXT NOT NULL, payload_sha TEXT NOT NULL,
            input_blob BLOB NOT NULL, input_sha TEXT NOT NULL,
            PRIMARY KEY(forecast_id,horizon)
        );
        """
    )
    db.execute("INSERT INTO pilot_meta(version) VALUES (?)", (LEDGER_VERSION,))
    for fid, direction in (("f-up", "UP"), ("f-down", "DOWN"), ("f-neutral", "NEUTRAL")):
        forecast = _forecast(fid, direction)
        raw = _encoded(forecast)
        source = f"source-{fid}".encode()
        receipt = {
            "forecast_id": fid, "symbol": "SBER", "secid": "SBER",
            "forecast_sha256": _sha(raw.encode()), "source_sha256": _sha(source),
            "captured_at": "2026-10-12T10:01:00Z",
            "decision_t0": "2026-10-12T10:00:00Z",
            "source_observed_at": "2026-10-12T09:59:00Z",
            "latest_completed_event_end": "2026-10-12T09:58:00Z",
        }
        receipt_raw = _encoded(receipt)
        db.execute(
            "INSERT INTO captures VALUES (?,?,?,?,?,?,?,?)",
            (fid, raw, _sha(raw.encode()), source, _sha(source), receipt_raw,
             _sha(receipt_raw.encode()), receipt["captured_at"]),
        )
        future_source = f"future-{fid}".encode()
        for horizon in (5, 10, 20):
            outcome = {
                "forecast_id": fid, "horizon_sessions": horizon, "market": "SBER",
                "secid": "SBER", "status": "OBSERVED", "target_session": "2026-10-20",
                "target_close": 110.0, "reference_price": 100.0,
                "actual_return_pct": 10.0,
                "direction_hit": True if direction == "UP" else False if direction == "DOWN" else None,
                "future_source_sha256": _sha(future_source),
                "target_session_end": "2026-10-20T16:00:00Z",
                "source_observed_at": "2026-10-20T17:00:00Z",
                "capture_at": "2026-10-12T10:01:00Z",
                "outcome_written_at": "2026-10-20T17:01:00Z", "pilot_only": True,
            }
            outcome_raw = _encoded(outcome)
            db.execute(
                "INSERT INTO outcomes VALUES (?,?,?,?,?,?)",
                (fid, horizon, outcome_raw, _sha(outcome_raw.encode()), future_source,
                 _sha(future_source)),
            )
    db.commit()
    db.close()


def test_scorecard_pairs_candidate_and_always_up_on_same_outcomes(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)

    report = build_scorecard(ledger)

    row = next(item for item in report["groups"] if item["horizon_sessions"] == 5)
    assert report["read_only"] is True
    assert row["forecasts_frozen"] == 3
    assert row["outcomes_completed"] == 3
    assert row["directional_forecasts_scored"] == 2
    assert row["candidate_correct"] == 1
    assert row["always_up_correct_same_rows"] == 2
    assert row["candidate_accuracy"] == 0.5
    assert row["always_up_accuracy_same_rows"] == 1.0
    assert row["neutral_forecasts"] == 1
    assert row["outcomes_pending"] == 0


def test_scorecard_refuses_a_changed_forecast_body(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE captures SET forecast_json='{}' WHERE forecast_id='f-up'")

    with pytest.raises(ScorecardRefused, match="capture evidence hash mismatch"):
        build_scorecard(ledger)


def test_scorecard_leaves_missing_outcomes_pending(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id='f-up' AND horizon=20")

    row = next(item for item in build_scorecard(ledger)["groups"] if item["horizon_sessions"] == 20)
    assert row["outcomes_completed"] == 2
    assert row["outcomes_pending"] == 1
    assert row["directional_forecasts_scored"] == 1
