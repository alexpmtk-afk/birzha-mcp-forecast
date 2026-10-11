from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.g2_prospective_baseline_scorecard import (
    DUCKDB_LEDGER_VERSION,
    LEDGER_VERSION,
    ScorecardRefused,
    build_scorecard,
    main,
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


def test_scorecard_refuses_rehashed_session_time_mismatch(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        raw = db.execute("SELECT payload_json FROM outcomes WHERE forecast_id='f-up' AND horizon=5").fetchone()[0]
        outcome = json.loads(raw)
        outcome["target_session"] = "2026-10-17"
        changed = _encoded(outcome)
        db.execute("UPDATE outcomes SET payload_json=?,payload_sha=? WHERE forecast_id='f-up' AND horizon=5", (changed, _sha(changed.encode())))
    with pytest.raises(ScorecardRefused, match="target session and close time disagree"):
        build_scorecard(ledger)


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal", ".wal"])
def test_scorecard_output_cannot_overwrite_ledger_or_sidecars(tmp_path: Path, suffix: str) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    before = ledger.read_bytes()
    output = Path(str(ledger) + suffix)
    assert main(["--ledger", str(ledger), "--output", str(output)]) == 2
    assert ledger.read_bytes() == before
    if suffix:
        assert not output.exists()


def test_scorecard_output_cannot_overwrite_a_hardlinked_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    alias = tmp_path / "report.json"
    alias.hardlink_to(ledger)
    before = ledger.read_bytes()
    assert main(["--ledger", str(ledger), "--output", str(alias)]) == 2
    assert ledger.read_bytes() == before


def _make_duckdb_ledger(path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    db = duckdb.connect(str(path))
    db.execute("CREATE TABLE g2_atomic_meta(version VARCHAR PRIMARY KEY)")
    db.execute("INSERT INTO g2_atomic_meta VALUES (?)", [DUCKDB_LEDGER_VERSION])
    db.execute("CREATE TABLE forecast_records(forecast_id VARCHAR, payload_hash VARCHAR, payload_json VARCHAR, symbol VARCHAR, secid VARCHAR, created_at_t0 VARCHAR)")
    db.execute("CREATE TABLE g2_atomic_captures(forecast_id VARCHAR, forecast_sha VARCHAR, source_blob BLOB, source_sha VARCHAR, receipt_json VARCHAR, receipt_sha VARCHAR)")
    db.execute("CREATE TABLE outcome_records(outcome_id VARCHAR, forecast_id VARCHAR, horizon_sessions INTEGER, payload_hash VARCHAR, payload_json VARCHAR)")
    db.execute("CREATE TABLE g2_atomic_futures(forecast_id VARCHAR, horizon INTEGER, outcome_id VARCHAR, source_blob BLOB, source_sha VARCHAR, evidence_json VARCHAR, evidence_sha VARCHAR)")

    forecast = _forecast("duck-f", "UP")
    raw_forecast = _encoded(forecast)
    source = b"original source bytes"
    receipt = {
        "forecast_id": "duck-f", "forecast_sha256": _sha(raw_forecast.encode()),
        "source_sha256": _sha(source), "secid": "SBER", "staging_only": True,
        "captured_at": "2026-10-12T10:01:00Z", "decision_t0": "2026-10-12T10:00:00Z",
        "source_observed_at": "2026-10-12T09:59:00Z",
        "latest_completed_event_end": "2026-10-12T09:58:00Z",
    }
    raw_receipt = _encoded(receipt)
    db.execute("INSERT INTO forecast_records VALUES (?,?,?,?,?,?)", [
        "duck-f", _sha(raw_forecast.encode()), raw_forecast, "SBER", "SBER", forecast["created_at_t0"]
    ])
    db.execute("INSERT INTO g2_atomic_captures VALUES (?,?,?,?,?,?)", [
        "duck-f", _sha(raw_forecast.encode()), source, _sha(source), raw_receipt, _sha(raw_receipt.encode())
    ])

    future_source = b"original future source bytes"
    outcome_id = "out_" + hashlib.sha256(b"duck-f:5").hexdigest()[:24]
    future_dates = ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"]
    future_input = {
        "source_sha256": _sha(future_source), "source_origin": "LIVE_COMPLETED_D1",
        "calendar_origin": "VERIFIED_EXCHANGE_SESSION_CALENDAR",
        "source_observed_at": "2026-10-26T17:00:00Z", "market": "SBER", "secid": "SBER",
        "session_dates": future_dates, "expected_calendar_dates": future_dates,
        "candle_completed_at": [f"{day}T16:00:00Z" for day in future_dates],
        "candle_close": [101.0, 103.0, 105.0, 107.0, 110.0],
    }
    future = {
        "forecast_id": "duck-f", "horizon_sessions": 5,
        "market": "SBER", "secid": "SBER", "status": "OBSERVED",
        "target_session": "2026-10-26", "target_session_end": "2026-10-26T16:00:00Z",
        "target_close": 110.0, "reference_price": 100.0, "actual_return_pct": 10.0,
        "direction_hit": True, "future_source_sha256": _sha(future_source),
        "calendar_sha256": _sha(_encoded(future_dates).encode()),
        "source_observed_at": "2026-10-26T17:00:00Z", "capture_at": "2026-10-12T10:01:00Z",
        "outcome_written_at": "2026-10-26T17:01:00Z",
        "future_input_evidence": future_input,
        "future_input_sha256": _sha(_encoded(future_input).encode()),
    }
    raw_future = _encoded(future)
    canonical_outcome = {
        "outcome_id": outcome_id, "forecast_id": "duck-f", "symbol": "SBER", "secid": "SBER",
        "horizon_sessions": 5, "reference_price": 100.0,
        "target_session_end": "2026-10-26T16:00:00Z", "target_close": 110.0,
        "actual_return_pct": 10.0, "direction_hit": True,
        "max_favorable_excursion_pct": None, "max_adverse_excursion_pct": None, "status": "OBSERVED",
    }
    raw_outcome = _encoded(canonical_outcome)
    db.execute("INSERT INTO g2_atomic_futures VALUES (?,?,?,?,?,?,?)", [
        "duck-f", 5, outcome_id, future_source, _sha(future_source), raw_future, _sha(raw_future.encode())
    ])
    db.execute("INSERT INTO outcome_records VALUES (?,?,?,?,?)", [
        outcome_id, "duck-f", 5, _sha(raw_outcome.encode()), raw_outcome
    ])
    db.close()


def test_scorecard_reads_canonical_duckdb_without_writing(tmp_path: Path) -> None:
    ledger = tmp_path / "staging.duckdb"
    _make_duckdb_ledger(ledger)
    before = _sha(ledger.read_bytes())

    report = build_scorecard(ledger)

    assert _sha(ledger.read_bytes()) == before
    assert report["read_only"] is True
    assert report["storage_format"] == "DuckDB staging journal"
    five = next(item for item in report["groups"] if item["horizon_sessions"] == 5)
    ten = next(item for item in report["groups"] if item["horizon_sessions"] == 10)
    assert five["outcomes_completed"] == 1
    assert five["outcomes_pending"] == 0
    assert five["candidate_correct"] == five["always_up_correct_same_rows"] == 1
    assert ten["outcomes_completed"] == 0
    assert ten["outcomes_pending"] == 1


def test_scorecard_refuses_duckdb_future_evidence_tampering(tmp_path: Path) -> None:
    duckdb = pytest.importorskip("duckdb")
    ledger = tmp_path / "staging.duckdb"
    _make_duckdb_ledger(ledger)
    db = duckdb.connect(str(ledger))
    db.execute("UPDATE g2_atomic_futures SET evidence_json='{}' WHERE horizon=5")
    db.close()

    with pytest.raises(ScorecardRefused, match="future evidence hash mismatch"):
        build_scorecard(ledger)
