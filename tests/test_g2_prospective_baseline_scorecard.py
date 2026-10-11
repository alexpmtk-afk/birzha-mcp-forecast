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
    render_scorecard_text,
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


def test_detail_rows_reconcile_with_every_summary_group(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    before = ledger.read_bytes()
    report = build_scorecard(ledger)
    assert len(report["observations"]) == 9
    assert len({(row["forecast_id"], row["horizon_sessions"]) for row in report["observations"]}) == 9
    for group in report["groups"]:
        rows = [row for row in report["observations"] if
                (row["market"], row["secid"], row["horizon_sessions"]) ==
                (group["market"], group["secid"], group["horizon_sessions"])]
        scored = [row for row in rows if row["comparison_included"]]
        assert group["forecasts_frozen"] == len(rows)
        assert group["outcomes_completed"] == sum(row["outcome_status"] == "OBSERVED" for row in rows)
        assert group["outcomes_pending"] == sum(row["outcome_status"] == "PENDING" for row in rows)
        assert group["directional_forecasts_scored"] == len(scored)
        assert group["candidate_correct"] == sum(row["candidate_correct"] for row in scored)
        assert group["always_up_correct_same_rows"] == sum(row["always_up_correct_same_row"] for row in scored)
        assert all(row["comparison_exclusion_reasons"] == [] for row in scored)
    neutral = next(row for row in report["observations"] if row["decision"] == "NEUTRAL")
    assert neutral["actual_return_pct"] == 10.0
    assert neutral["comparison_exclusion_reasons"] == ["NEUTRAL_DECISION"]
    assert neutral["candidate_correct"] is None
    assert neutral["always_up_correct_same_row"] is None
    assert ledger.read_bytes() == before


def test_pending_details_keep_unknown_values_and_exclusion_reasons(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes")
    report = build_scorecard(ledger)
    for row in report["observations"]:
        assert row["outcome_status"] == "PENDING"
        assert row["comparison_included"] is False
        assert "PENDING_OUTCOME" in row["comparison_exclusion_reasons"]
        for field in ("actual_return_pct", "target_close", "target_session", "target_session_end",
                      "future_source_sha256", "candidate_correct", "always_up_correct_same_row"):
            assert row[field] is None
    text = render_scorecard_text(report, include_details=True)
    assert text.count("Запись:") == 9
    assert "результат ещё не получен; нет направленного решения" in text
    assert "Прогноз: ошибка" not in text


def test_cli_details_show_the_exact_forecast_and_its_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    before = ledger.read_bytes()
    assert main(["--ledger", str(ledger), "--format", "text", "--details"]) == 0
    text = capsys.readouterr().out
    assert "Запись: f-down. Рынок: SBER. Инструмент: SBER." in text
    assert "Прогноз: ошибка. Правило «всегда рост»: верно." in text
    assert "Не участвует в сравнении: нет направленного решения." in text
    assert ledger.read_bytes() == before


def test_scorecard_refuses_a_changed_forecast_body(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE captures SET forecast_json='{}' WHERE forecast_id='f-up'")

    with pytest.raises(ScorecardRefused, match="capture evidence hash mismatch"):
        build_scorecard(ledger)


@pytest.mark.parametrize("storage", ["sqlite3", "duckdb"])
def test_report_is_repeatable_after_reopening_and_copying(tmp_path: Path, storage: str) -> None:
    ledger = tmp_path / ("original." + storage)
    (_make_ledger if storage == "sqlite3" else _make_duckdb_ledger)(ledger)
    before = ledger.read_bytes()
    first = build_scorecard(ledger)
    second = build_scorecard(ledger)
    copied = tmp_path / ("different-name." + storage)
    copied.write_bytes(before)
    third = build_scorecard(copied)
    assert first == second == third
    assert render_scorecard_text(first, include_details=True) == render_scorecard_text(third, include_details=True)
    unsigned = dict(first)
    checksum = unsigned.pop("report_sha256")
    assert checksum == _sha(_encoded(unsigned).encode())
    assert ledger.read_bytes() == copied.read_bytes() == before
    for row in first["observations"]:
        assert len(row["capture_receipt_sha256"]) == len(row["capture_source_sha256"]) == 64
        assert (row["outcome_sha256"] is not None) == (row["outcome_status"] == "OBSERVED")


def test_report_is_independent_of_physical_row_order(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    first = build_scorecard(ledger)
    with sqlite3.connect(ledger) as db:
        for table in ("captures", "outcomes"):
            rows = db.execute(f"SELECT * FROM {table}").fetchall()
            db.execute(f"DELETE FROM {table}")
            placeholders = ",".join("?" for _ in rows[0])
            db.executemany(f"INSERT INTO {table} VALUES ({placeholders})", reversed(rows))
    assert build_scorecard(ledger) == first


def test_report_checksum_changes_when_an_outcome_is_still_pending(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    first = build_scorecard(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id='f-down' AND horizon=5")
    second = build_scorecard(ledger)
    assert first["report_sha256"] != second["report_sha256"]
    row = next(row for row in second["observations"] if row["forecast_id"] == "f-down" and row["horizon_sessions"] == 5)
    assert row["outcome_sha256"] is None
    assert row["candidate_correct"] is None


def test_duplicate_sqlite_outcome_is_refused_without_counting_it_twice(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE duplicates AS SELECT * FROM outcomes")
        db.execute("INSERT INTO duplicates SELECT * FROM outcomes WHERE forecast_id='f-up' AND horizon=5")
        db.execute("DROP TABLE outcomes")
        db.execute("ALTER TABLE duplicates RENAME TO outcomes")
    before = ledger.read_bytes()
    with pytest.raises(ScorecardRefused, match="duplicate outcome identity"):
        build_scorecard(ledger)
    assert ledger.read_bytes() == before


def test_text_report_keeps_directional_denominator_and_reference_rows(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    text = render_scorecard_text(build_scorecard(ledger))
    assert "Исходов завершено: 3. Ожидают результата: 0." in text
    assert "Проверено направленных прогнозов: 2." in text
    assert "Верно: 1 из 2. Ошибок: 1. Точность: 50,00%." in text
    assert "Правило «всегда рост»: верно 2 из 2; точность 100,00%." in text
    assert "Разница с правилом «всегда рост»: -50,00 процентных пунктов." in text
    assert "Нейтральных: 1. Явных отказов: 0." in text


def test_text_report_does_not_show_pending_accuracy_as_zero(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes")
    text = render_scorecard_text(build_scorecard(ledger))
    assert "Ожидают результата: 3." in text
    assert "Точность пока неизвестна" in text
    assert "Верно: 0" not in text
    assert "Точность: 0,00%" not in text


def test_text_report_distinguishes_completed_neutral_from_waiting(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id != 'f-neutral'")
        db.execute("DELETE FROM captures WHERE forecast_id != 'f-neutral'")
    text = render_scorecard_text(build_scorecard(ledger))
    assert "Исходов завершено: 1. Ожидают результата: 0." in text
    assert "Точность не рассчитывается: направленных решений нет." in text
    assert "направленные прогнозы ожидают результата" not in text


def test_cli_saves_russian_report_without_changing_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    before = ledger.read_bytes()
    output = tmp_path / "report.txt"
    assert main(["--ledger", str(ledger), "--format", "text", "--output", str(output)]) == 0
    assert "Верно: 1 из 2. Ошибок: 1." in output.read_text(encoding="utf-8")
    assert ledger.read_bytes() == before


def test_cli_preserves_default_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    assert main(["--ledger", str(ledger)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["read_only"] is True
    assert report["groups"][0]["candidate_accuracy"] == .5


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
