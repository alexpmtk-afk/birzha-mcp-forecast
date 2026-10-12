from __future__ import annotations

import hashlib
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import shutil
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
from scripts.g2_prospective_scorecard_review import (
    load_scorecard, main as review_main, match_ledger, render_scorecard_csv, validate_scorecard,
)
from scripts.g2_prospective_scorecard_batch import (
    build_package, combine_scorecards, main as batch_main, verify_package,
)
import scripts.g2_prospective_scorecard_batch as batch_module
from scripts.g2_prospective_scorecard_changes import (
    compare_packages, compare_scorecards, main as changes_main, render_changes_text,
)
import scripts.g2_prospective_scorecard_changes as changes_module


def _encoded(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _forecast(fid: str, direction: str) -> dict[str, object]:
    return {
        "record_version": "FORECAST_RECORD_V1_PROTOCOL_08",
        "engine_version": "SYNTHETIC_SCORECARD_TEST",
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
    coverage = report["coverage_summary"]
    assert coverage["forecast_horizon_rows"] == 9
    assert coverage["directional_rows"] == 6
    assert coverage["neutral_rows"] == 3
    assert coverage["explicit_abstention_rows"] == 0
    assert coverage["directional_coverage"] == 6 / 9
    assert coverage["abstention_reasons"] == []
    assert len(coverage["by_market"]) == 1
    assert coverage["by_market"][0]["market"] == "SBER"
    assert coverage["by_market"][0]["directional_rows"] == 6
    assert len(coverage["by_horizon"]) == 3
    assert all(item["forecast_horizon_rows"] == 3 and item["directional_rows"] == 2
               for item in coverage["by_horizon"])
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


def _resign_report(report: dict[str, object]) -> None:
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    report["report_sha256"] = _sha(_encoded(unsigned).encode())


@pytest.mark.parametrize("storage", ["sqlite3", "duckdb"])
def test_review_loads_matches_and_exports_both_journal_formats(tmp_path: Path, storage: str) -> None:
    ledger = tmp_path / ("pilot." + storage)
    (_make_ledger if storage == "sqlite3" else _make_duckdb_ledger)(ledger)
    before = ledger.read_bytes()
    report = build_scorecard(ledger)
    saved = tmp_path / "report.json"
    saved.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    assert load_scorecard(saved) == report
    match_ledger(report, ledger)
    output = tmp_path / "decisions.csv"
    assert review_main(["--report", str(saved), "--match-ledger", str(ledger), "--format", "csv", "--output", str(output)]) == 0
    rows = list(csv.DictReader(io.StringIO(output.read_text(encoding="utf-8-sig")), delimiter=";"))
    assert len(rows) == len(report["observations"])
    assert all(row["Контрольная сумма отчёта"] == report["report_sha256"] for row in rows)
    assert output.read_bytes().startswith(b"\xef\xbb\xbf")
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("defect", [
    "counts", "boolean_count", "accuracy", "boolean_accuracy", "duplicate_row", "missing_horizon",
    "missing_group", "duplicate_group", "included", "correctness", "return", "reference",
    "exclusion", "capture_time", "target_time", "origin_binding", "hash", "unknown_direction",
    "unknown_status", "version", "evidence_claim", "limits", "coverage", "market_coverage",
    "horizon_coverage", "abstention_reason", "abstention_field_flag", "numeric_error", "numeric_price",
    "flat_price_error", "target_level", "extra_field",
])
def test_review_refuses_resigned_but_inconsistent_reports(tmp_path: Path, defect: str) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    report = build_scorecard(ledger)
    row = report["observations"][0]
    group = report["groups"][0]
    if defect == "counts":
        group["forecasts_frozen"] += 1
    elif defect == "boolean_count":
        group["candidate_correct"] = True
    elif defect == "accuracy":
        group["candidate_accuracy"] = .9
    elif defect == "boolean_accuracy":
        group["always_up_accuracy_same_rows"] = True
    elif defect == "duplicate_row":
        report["observations"].append(dict(row))
    elif defect == "missing_horizon":
        report["observations"].pop(0)
    elif defect == "missing_group":
        report["groups"].pop(0)
    elif defect == "duplicate_group":
        report["groups"].append(dict(group))
    elif defect == "included":
        row["comparison_included"] = False
    elif defect == "correctness":
        row["candidate_correct"] = not row["candidate_correct"]
    elif defect == "return":
        row["actual_return_pct"] = 20.
    elif defect == "reference":
        row["reference_price"] = 0.
    elif defect == "exclusion":
        row["comparison_exclusion_reasons"] = ["PENDING_OUTCOME"]
    elif defect == "capture_time":
        row["captured_at"] = "2026-10-11T10:00:00Z"
    elif defect == "target_time":
        row["target_session_end"] = "2026-10-12T09:00:00Z"
    elif defect == "origin_binding":
        row["capture_source_sha256"] = "a" * 64
    elif defect == "hash":
        row["outcome_sha256"] = "wrong"
    elif defect == "unknown_direction":
        row["decision"] = []
    elif defect == "unknown_status":
        row["outcome_status"] = "UNKNOWN"
    elif defect == "version":
        report["ledger_version"] = []
    elif defect == "evidence_claim":
        report["future_evidence_validation"] = "Independent source authenticated"
    elif defect == "limits":
        report["limits"] = []
    elif defect == "coverage":
        report["coverage_summary"]["directional_rows"] += 1
    elif defect == "market_coverage":
        report["coverage_summary"]["by_market"][0]["directional_rows"] += 1
    elif defect == "horizon_coverage":
        report["coverage_summary"]["by_horizon"][0]["directional_coverage"] = 1.
    elif defect == "abstention_reason":
        row["abstention_reasons"] = ["invented cause"]
    elif defect == "abstention_field_flag":
        row["explicit_abstention_fields_present"] = None
    elif defect == "numeric_error":
        row["absolute_move_error_percentage_points"] = 1.0
    elif defect == "numeric_price":
        row["implied_target_price"] = 100.0
    elif defect == "flat_price_error":
        row["flat_price_absolute_error_percentage_points"] = 0.0
    elif defect == "target_level":
        row["target_level"] = 100.0
    else:
        row["unsupported"] = True
    _resign_report(report)
    before = ledger.read_bytes()
    with pytest.raises(ScorecardRefused):
        validate_scorecard(report)
    assert ledger.read_bytes() == before


def test_review_detects_a_stale_report_against_the_unchanged_forecast_journal(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    saved = build_scorecard(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id='f-up' AND horizon=5")
    assert validate_scorecard(saved) == saved
    before = ledger.read_bytes()
    with pytest.raises(ScorecardRefused, match="не совпадает с указанным журналом"):
        match_ledger(saved, ledger)
    assert ledger.read_bytes() == before


@pytest.mark.parametrize("raw", ['{"groups":[],"groups":[]}', '{"bad":NaN}', '{"bad":Infinity}', '[]', '{broken'])
def test_review_load_refuses_ambiguous_or_invalid_json(tmp_path: Path, raw: str) -> None:
    saved = tmp_path / "report.json"
    saved.write_text(raw, encoding="utf-8")
    with pytest.raises(ScorecardRefused):
        load_scorecard(saved)
    assert saved.read_text(encoding="utf-8") == raw


def test_review_detects_checksum_changes_before_export_and_keeps_existing_output(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    report = build_scorecard(ledger)
    report["groups"][0]["candidate_accuracy"] = .9
    saved = tmp_path / "report.json"
    saved.write_text(json.dumps(report), encoding="utf-8")
    output = tmp_path / "existing.csv"
    output.write_text("keep existing result", encoding="utf-8")
    assert review_main(["--report", str(saved), "--format", "csv", "--output", str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "keep existing result"


def test_csv_preserves_pending_neutral_and_abstention_without_false_scores(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id='f-up'")
        raw, receipt_raw = db.execute("SELECT forecast_json,receipt_json FROM captures WHERE forecast_id='f-neutral'").fetchone()
        forecast, receipt = json.loads(raw), json.loads(receipt_raw)
        forecast["decision_status"] = "ABSTAIN"
        forecast["abstention_reasons"] = ["synthetic insufficient input"]
        encoded = _encoded(forecast)
        receipt["forecast_sha256"] = _sha(encoded.encode())
        encoded_receipt = _encoded(receipt)
        db.execute("UPDATE captures SET forecast_json=?,forecast_sha=?,receipt_json=?,receipt_sha=? WHERE forecast_id='f-neutral'",
                   (encoded, receipt["forecast_sha256"], encoded_receipt, _sha(encoded_receipt.encode())))
    report = build_scorecard(ledger)
    rows = list(csv.DictReader(io.StringIO(render_scorecard_csv(report)), delimiter=";"))
    for row in rows:
        if row["Номер прогноза"] in ("f-up", "f-neutral"):
            assert row["Результат прогноза"] == row["Результат правила всегда рост"] == ""
        if row["Номер прогноза"] == "f-up":
            assert row["Цена закрытия"] == row["Изменение цены в процентах"] == ""
        if row["Номер прогноза"] == "f-neutral":
            assert row["Направление"] == "явный отказ"
            assert row["Изменение цены в процентах"] == "10.0"
            assert row["Участвует в сравнении"] == "нет"
            assert row["Исходные причины явного отказа"] == "synthetic insufficient input"
    abstaining = [row for row in report["observations"] if row["decision"] == "ABSTAIN"]
    assert len(abstaining) == 3
    assert all(row["abstention_reasons"] == ["synthetic insufficient input"] for row in abstaining)
    assert report["coverage_summary"]["explicit_abstention_rows"] == 3
    assert report["coverage_summary"]["rows_with_explicit_abstention_fields"] == 3
    assert all(row["explicit_abstention_fields_present"] is True for row in abstaining)
    assert all(item["rows_with_explicit_abstention_fields"] == 1
               for item in report["coverage_summary"]["by_horizon"])
    assert report["coverage_summary"]["directional_coverage"] == 6 / 9
    assert report["coverage_summary"]["abstention_reasons"] == [
        {"reason": "synthetic insufficient input", "forecast_horizon_rows": 3, "unique_forecasts": 1}
    ]
    assert report["coverage_summary"]["by_market"][0]["abstention_reasons"] == [
        {"reason": "synthetic insufficient input", "forecast_horizon_rows": 3, "unique_forecasts": 1}
    ]
    assert all(item["abstention_reasons"] == [
        {"reason": "synthetic insufficient input", "forecast_horizon_rows": 1, "unique_forecasts": 1}
    ] for item in report["coverage_summary"]["by_horizon"])
    text = render_scorecard_text(report)
    assert "synthetic insufficient input" in text
    assert "3 записей сроков, 1 прогноз" in text
    assert "Охват отдельно по срокам" in text
    assert "Охват отдельно по точным инструментам" in text


def _set_synthetic_expected_move(ledger: Path, value: object) -> None:
    """Change only a synthetic forecast and its matching capture hashes."""
    with sqlite3.connect(ledger) as db:
        raw, receipt_raw = db.execute(
            "SELECT forecast_json,receipt_json FROM captures WHERE forecast_id='f-up'"
        ).fetchone()
        forecast, receipt = json.loads(raw), json.loads(receipt_raw)
        for horizon in forecast["horizons"]:
            horizon["expected_move_pct"] = value
        encoded = _encoded(forecast)
        receipt["forecast_sha256"] = _sha(encoded.encode())
        encoded_receipt = _encoded(receipt)
        db.execute(
            "UPDATE captures SET forecast_json=?,forecast_sha=?,receipt_json=?,receipt_sha=? WHERE forecast_id='f-up'",
            (encoded, receipt["forecast_sha256"], encoded_receipt, _sha(encoded_receipt.encode())),
        )


def _set_synthetic_levels(ledger: Path) -> None:
    with sqlite3.connect(ledger) as db:
        raw, receipt_raw = db.execute(
            "SELECT forecast_json,receipt_json FROM captures WHERE forecast_id='f-up'"
        ).fetchone()
        forecast, receipt = json.loads(raw), json.loads(receipt_raw)
        forecast["target_levels"] = [105.0]
        forecast["stop_level"] = 95.0
        encoded = _encoded(forecast)
        receipt["forecast_sha256"] = _sha(encoded.encode())
        encoded_receipt = _encoded(receipt)
        db.execute(
            "UPDATE captures SET forecast_json=?,forecast_sha=?,receipt_json=?,receipt_sha=? WHERE forecast_id='f-up'",
            (encoded, receipt["forecast_sha256"], encoded_receipt, _sha(encoded_receipt.encode())),
        )


def test_target_and_stop_levels_are_checked_only_at_terminal_close(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    _set_synthetic_levels(ledger)
    observed_ledger = tmp_path / "observed.sqlite3"
    shutil.copy2(ledger, observed_ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes")
    pending = build_scorecard(ledger)
    assert all(row["target_level"] == 105.0 and row["stop_level"] == 95.0
               and row["target_close_status"] is None and row["stop_close_status"] is None
               for row in pending["observations"] if row["forecast_id"] == "f-up")
    report = build_scorecard(observed_ledger)
    validate_scorecard(report)
    match_ledger(report, observed_ledger)
    assert all(row["target_close_status"] == "REACHED_AT_CLOSE"
               and row["stop_close_status"] == "NOT_REACHED_AT_CLOSE"
               for row in report["observations"] if row["forecast_id"] == "f-up")
    text = render_scorecard_text(report, include_details=True)
    assert "Проверка по конечной цене: REACHED_AT_CLOSE" in text
    assert "Проверка по конечной цене: NOT_REACHED_AT_CLOSE" in text


def test_numeric_move_forecast_is_scored_only_after_a_realized_outcome(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    _set_synthetic_expected_move(ledger, 5.0)
    report = build_scorecard(ledger)
    validate_scorecard(report)
    match_ledger(report, ledger)
    assert report["coverage_summary"]["numeric_move_rows"] == 3
    assert report["coverage_summary"]["numeric_move_scored_rows"] == 3
    for row in report["observations"]:
        if row["forecast_id"] == "f-up":
            assert row["expected_move_pct"] == 5.0
            assert row["implied_target_price"] == 105.0
            assert row["absolute_move_error_percentage_points"] == 5.0
            assert row["flat_price_absolute_error_percentage_points"] == 10.0
        else:
            assert row["expected_move_pct"] is None
            assert row["implied_target_price"] is None
            assert row["absolute_move_error_percentage_points"] is None
            assert row["flat_price_absolute_error_percentage_points"] is None
    assert all(group["numeric_move_forecasts"] == 1 and
               group["numeric_move_forecasts_scored"] == 1 and
               group["mean_absolute_move_error_percentage_points"] == 5.0 and
               group["flat_price_mean_absolute_error_percentage_points"] == 10.0 and
               group["numeric_minus_flat_error_percentage_points"] == -5.0
               for group in report["groups"])
    csv_rows = list(csv.DictReader(io.StringIO(render_scorecard_csv(report)), delimiter=";"))
    assert all(row["Расчётная цена в конце срока"] == "105.0" for row in csv_rows if row["Номер прогноза"] == "f-up")
    assert "Средняя абсолютная ошибка движения" in render_scorecard_text(report)

    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id='f-up'")
    pending = build_scorecard(ledger)
    validate_scorecard(pending)
    assert pending["coverage_summary"]["numeric_move_rows"] == 3
    assert pending["coverage_summary"]["numeric_move_scored_rows"] == 0
    assert all(row["absolute_move_error_percentage_points"] is None
               for row in pending["observations"] if row["forecast_id"] == "f-up")
    assert all(group["mean_absolute_move_error_percentage_points"] is None and
               group["flat_price_mean_absolute_error_percentage_points"] is None and
               group["numeric_minus_flat_error_percentage_points"] is None
               for group in pending["groups"])


@pytest.mark.parametrize("value", [True, -100.0, "five"])
def test_numeric_move_refuses_invalid_frozen_prediction(tmp_path: Path, value: object) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    _set_synthetic_expected_move(ledger, value)
    with pytest.raises(ScorecardRefused):
        build_scorecard(ledger)


@pytest.mark.parametrize("name", ['=1+1', ' +SUM(1;2)', '@command', '-formula', 'quoted;"name\nnext'])
def test_csv_quotes_text_without_executing_formula_like_identifiers(tmp_path: Path, name: str) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    report = build_scorecard(ledger)
    for row in report["observations"]:
        if row["forecast_id"] == "f-up":
            row["forecast_id"] = name
    _resign_report(report)
    rows = list(csv.DictReader(io.StringIO(render_scorecard_csv(report)), delimiter=";"))
    expected = "'" + name if name.lstrip().startswith(("=", "+", "-", "@")) else name
    assert sum(row["Номер прогноза"] == expected for row in rows) == 3
    assert any(row["forecast_id"] == name for row in report["observations"])


@pytest.mark.parametrize("target", ["report", "ledger", "wal", "hardlink", "symlink"])
def test_review_output_cannot_replace_evidence_or_an_alias(tmp_path: Path, target: str) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    saved = tmp_path / "report.json"
    saved.write_text(json.dumps(build_scorecard(ledger)), encoding="utf-8")
    if target == "report":
        output = saved
    elif target == "ledger":
        output = ledger
    elif target == "wal":
        output = Path(str(ledger) + "-wal")
        output.write_bytes(b"preserve sidecar")
    else:
        output = tmp_path / "alias"
        if target == "hardlink":
            os.link(saved, output)
        else:
            output.symlink_to(saved)
    before = output.read_bytes()
    assert review_main(["--report", str(saved), "--match-ledger", str(ledger), "--format", "csv", "--output", str(output)]) == 2
    assert output.read_bytes() == before


def test_review_cli_check_only_reports_its_actual_scope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    saved = tmp_path / "report.json"
    saved.write_text(json.dumps(build_scorecard(ledger)), encoding="utf-8")
    assert review_main(["--report", str(saved), "--check-only"]) == 0
    assert "для связи с исходными данными укажите журнал" in capsys.readouterr().out
    assert review_main(["--report", str(saved), "--match-ledger", str(ledger), "--check-only"]) == 0
    assert "Связь с журналом проверена" in capsys.readouterr().out


@pytest.mark.parametrize("field", ["actual_return_pct", "target_close", "candidate_correct", "always_up_correct_same_row", "outcome_sha256"])
def test_review_refuses_values_filled_into_pending_results(tmp_path: Path, field: str) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes")
    report = build_scorecard(ledger)
    report["observations"][0][field] = "a" * 64 if field == "outcome_sha256" else False if "correct" in field else 0.
    _resign_report(report)
    with pytest.raises(ScorecardRefused, match="Ожидающий исход"):
        validate_scorecard(report)


@pytest.mark.parametrize("return_pct,expected_up,expected_down", [(10., True, False), (-10., False, True), (0., False, False)])
def test_review_recalculates_both_rules_with_flat_return_counted_as_wrong(
    tmp_path: Path, return_pct: float, expected_up: bool, expected_down: bool,
) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        for fid, horizon, raw in db.execute("SELECT forecast_id,horizon,payload_json FROM outcomes").fetchall():
            outcome = json.loads(raw)
            outcome["target_close"] = 100. + return_pct
            outcome["actual_return_pct"] = return_pct
            outcome["direction_hit"] = expected_up if fid == "f-up" else expected_down if fid == "f-down" else None
            encoded = _encoded(outcome)
            db.execute("UPDATE outcomes SET payload_json=?,payload_sha=? WHERE forecast_id=? AND horizon=?",
                       (encoded, _sha(encoded.encode()), fid, horizon))
    report = validate_scorecard(build_scorecard(ledger))
    rows = {row["forecast_id"]: row for row in report["observations"] if row["horizon_sessions"] == 5}
    assert rows["f-up"]["candidate_correct"] is expected_up
    assert rows["f-down"]["candidate_correct"] is expected_down
    assert rows["f-up"]["always_up_correct_same_row"] is expected_up
    assert rows["f-down"]["always_up_correct_same_row"] is expected_up


def test_review_accepts_empty_journal_without_inventing_groups(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes")
        db.execute("DELETE FROM captures")
    report = validate_scorecard(build_scorecard(ledger))
    assert report["groups"] == report["observations"] == []
    assert len(list(csv.reader(io.StringIO(render_scorecard_csv(report)), delimiter=";"))) == 1


def test_review_direct_script_runs_from_another_directory(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    script = Path(__file__).resolve().parents[1] / "scripts/g2_prospective_scorecard_review.py"
    before = ledger.read_bytes()
    completed = subprocess.run([sys.executable, str(script), "--ledger", str(ledger), "--check-only"],
                               cwd=tmp_path, text=True, capture_output=True, encoding="utf-8", check=False,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert completed.returncode == 0, completed.stderr
    assert "Отчёт согласован" in completed.stdout
    assert ledger.read_bytes() == before


def _disjoint_copy(source: Path, target: Path) -> None:
    target.write_bytes(source.read_bytes())
    with sqlite3.connect(target) as db:
        for fid, raw, receipt_raw in db.execute("SELECT forecast_id,forecast_json,receipt_json FROM captures").fetchall():
            forecast, receipt = json.loads(raw), json.loads(receipt_raw)
            new_id = fid + "-second"
            forecast["forecast_id"] = receipt["forecast_id"] = new_id
            encoded = _encoded(forecast)
            receipt["forecast_sha256"] = _sha(encoded.encode())
            encoded_receipt = _encoded(receipt)
            db.execute("UPDATE captures SET forecast_id=?,forecast_json=?,forecast_sha=?,receipt_json=?,receipt_sha=? WHERE forecast_id=?",
                       (new_id, encoded, receipt["forecast_sha256"], encoded_receipt, _sha(encoded_receipt.encode()), fid))
            for horizon, payload in db.execute("SELECT horizon,payload_json FROM outcomes WHERE forecast_id=?", (fid,)).fetchall():
                outcome = json.loads(payload)
                outcome["forecast_id"] = new_id
                encoded_outcome = _encoded(outcome)
                db.execute("UPDATE outcomes SET forecast_id=?,payload_json=?,payload_sha=? WHERE forecast_id=? AND horizon=?",
                           (new_id, encoded_outcome, _sha(encoded_outcome.encode()), fid, horizon))


def test_batch_pools_same_instrument_without_averaging_accuracy(tmp_path: Path) -> None:
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    _make_ledger(first)
    _set_synthetic_expected_move(first, 5.0)
    _disjoint_copy(first, second)
    with sqlite3.connect(second) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id='f-down-second'")
    a, b = build_scorecard(first), build_scorecard(second)
    combined = combine_scorecards([a, b])
    assert combined == combine_scorecards([b, a])
    group = combined["groups"][0]
    assert group["forecasts_frozen"] == 6
    assert group["outcomes_completed"] == 5
    assert group["outcomes_pending"] == 1
    assert group["directional_forecasts_scored"] == 3
    assert group["candidate_correct"] == 2
    assert group["candidate_accuracy"] == 2 / 3
    assert group["candidate_accuracy"] != (a["groups"][0]["candidate_accuracy"] + b["groups"][0]["candidate_accuracy"]) / 2
    assert group["always_up_accuracy_same_rows"] == 1.
    assert group["numeric_move_forecasts"] == 2
    assert group["numeric_move_forecasts_scored"] == 2
    assert group["mean_absolute_move_error_percentage_points"] == 5.0
    assert group["flat_price_mean_absolute_error_percentage_points"] == 10.0
    assert group["numeric_minus_flat_error_percentage_points"] == -5.0
    coverage = combined["coverage_summary"]
    assert coverage["forecast_horizon_rows"] == 18
    assert coverage["directional_rows"] == 12
    assert coverage["neutral_rows"] == 6
    assert coverage["completed_outcome_rows"] == 15
    assert coverage["pending_outcome_rows"] == 3
    assert coverage["by_market"][0]["forecast_horizon_rows"] == 18
    assert [row["horizon_sessions"] for row in coverage["by_horizon"]] == [5, 10, 20]
    assert all(row["forecast_horizon_rows"] == 6 and row["directional_rows"] == 4
               for row in coverage["by_horizon"])


@pytest.mark.parametrize("storage", ["sqlite3", "duckdb"])
def test_batch_builds_and_rechecks_complete_package(tmp_path: Path, storage: str) -> None:
    first = tmp_path / ("first." + storage)
    (_make_ledger if storage == "sqlite3" else _make_duckdb_ledger)(first)
    output = tmp_path / "package"
    before = first.read_bytes()
    manifest = build_package([first], output)
    assert verify_package(output) == manifest
    assert load_scorecard(output / "combined.json") == build_scorecard(first)
    assert first.read_bytes() == before
    assert len(list(output.iterdir())) == 7
    assert not list(tmp_path.glob(".g2-scorecard-package-*"))


@pytest.mark.parametrize("alias", ["same", "copy", "hardlink", "symlink"])
def test_batch_refuses_duplicate_sources_and_forecasts(tmp_path: Path, alias: str) -> None:
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    _make_ledger(first)
    if alias == "same":
        second = first
    elif alias == "copy":
        second.write_bytes(first.read_bytes())
    elif alias == "hardlink":
        os.link(first, second)
    else:
        second.symlink_to(first)
    with pytest.raises(ScorecardRefused):
        build_package([first, second], tmp_path / "package")
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".g2-scorecard-package-*"))


@pytest.mark.parametrize("defect", ["engine", "unknown_engine", "record", "storage"])
def test_batch_refuses_mixed_or_unknown_versions(tmp_path: Path, defect: str) -> None:
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    _make_ledger(first)
    _disjoint_copy(first, second)
    a, b = build_scorecard(first), build_scorecard(second)
    if defect in ("engine", "unknown_engine", "record"):
        for row in b["observations"]:
            if defect == "record":
                row["forecast_record_version"] = "FORECAST_RECORD_V2_BASELINE_EVIDENCE"
            else:
                row["engine_version"] = None if defect == "unknown_engine" else "OTHER_ENGINE"
    else:
        duck = tmp_path / "staging.duckdb"
        _make_duckdb_ledger(duck)
        b = build_scorecard(duck)
    _resign_report(b)
    validate_scorecard(b)
    with pytest.raises(ScorecardRefused):
        combine_scorecards([a, b])


def test_batch_never_overwrites_an_existing_output_directory(tmp_path: Path) -> None:
    first = tmp_path / "first.sqlite3"
    _make_ledger(first)
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    assert batch_main(["--ledger", str(first), "--output-dir", str(output)]) == 2
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert {path.name for path in output.iterdir()} == {"keep.txt"}


@pytest.mark.parametrize("member", ["combined.csv", "combined.json", "journal_001.txt", "manifest.json", "extra.txt"])
def test_batch_verifier_refuses_modified_or_extra_files(tmp_path: Path, member: str) -> None:
    ledger = tmp_path / "first.sqlite3"
    _make_ledger(ledger)
    output = tmp_path / "package"
    build_package([ledger], output)
    with (output / member).open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ScorecardRefused):
        verify_package(output)


def test_batch_verifier_refuses_resigned_export_tampering(tmp_path: Path) -> None:
    ledger = tmp_path / "first.sqlite3"
    _make_ledger(ledger)
    output = tmp_path / "package"
    build_package([ledger], output)
    changed = b"wrong table"
    (output / "combined.csv").write_bytes(changed)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["combined.csv"] = _sha(changed)
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    manifest["manifest_sha256"] = _sha(_encoded(unsigned).encode())
    (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ScorecardRefused, match="Содержимое файла"):
        verify_package(output)


def test_batch_refuses_a_journal_changed_during_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "first.sqlite3"
    _make_ledger(ledger)
    original = batch_module.build_scorecard
    calls = [0]
    def changing(path: Path) -> dict[str, object]:
        calls[0] += 1
        if calls[0] == 2:
            with sqlite3.connect(path) as db:
                db.execute("DELETE FROM outcomes WHERE horizon=5")
        return original(path)
    monkeypatch.setattr(batch_module, "build_scorecard", changing)
    with pytest.raises(ScorecardRefused, match="изменился во время"):
        build_package([ledger], tmp_path / "package")
    assert not (tmp_path / "package").exists()
    assert not list(tmp_path.glob(".g2-scorecard-package-*"))


def test_batch_package_content_is_independent_of_input_order(tmp_path: Path) -> None:
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    _make_ledger(first)
    _disjoint_copy(first, second)
    a, b = tmp_path / "a", tmp_path / "b"
    assert build_package([first, second], a) == build_package([second, first], b)
    assert {path.name: path.read_bytes() for path in a.iterdir()} == {path.name: path.read_bytes() for path in b.iterdir()}
    assert batch_main(["--verify-package", str(a)]) == 0


def test_changes_keep_identical_packages_and_pending_values_unchanged(tmp_path: Path) -> None:
    ledger = tmp_path / "pending.sqlite3"
    _make_ledger(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes")
    package = tmp_path / "package"
    build_package([ledger], package)
    before = {path.name: path.read_bytes() for path in package.iterdir()}
    result = compare_packages(package, package)
    assert result["new_forecast_ids"] == result["newly_observed_existing_rows"] == []
    assert result["existing_pending_rows_unchanged"] == result["pending_rows_now"] == 9
    assert all(row["candidate_accuracy_change_percentage_points"] is None for row in result["group_changes"])
    assert "Точность пока не рассчитана" in render_changes_text(result)
    assert {path.name: path.read_bytes() for path in package.iterdir()} == before
    unsigned = {key: value for key, value in result.items() if key != "change_sha256"}
    assert result["change_sha256"] == _sha(_encoded(unsigned).encode())


@pytest.mark.parametrize("storage", ["sqlite3", "duckdb"])
def test_changes_report_newly_observed_existing_rows_and_neutral_exclusion(tmp_path: Path, storage: str) -> None:
    ledger = tmp_path / ("pilot." + storage)
    (_make_ledger if storage == "sqlite3" else _make_duckdb_ledger)(ledger)
    after = build_scorecard(ledger)
    if storage == "sqlite3":
        with sqlite3.connect(ledger) as db:
            db.execute("DELETE FROM outcomes")
    else:
        duckdb = pytest.importorskip("duckdb")
        db = duckdb.connect(str(ledger))
        db.execute("DELETE FROM outcome_records")
        db.execute("DELETE FROM g2_atomic_futures")
        db.close()
    before = build_scorecard(ledger)
    result = compare_scorecards(before, after)
    expected = 9 if storage == "sqlite3" else 1
    assert len(result["newly_observed_existing_rows"]) == expected
    assert result["new_forecast_ids"] == []
    assert all(row["candidate_accuracy_change_percentage_points"] is None for row in result["group_changes"])
    if storage == "sqlite3":
        neutral = [row for row in result["newly_observed_existing_rows"] if row["decision"] == "NEUTRAL"]
        assert len(neutral) == 3
        assert all(row["candidate_correct"] is None for row in neutral)
        assert result["group_changes"][0]["after"]["directional_forecasts_scored"] == 2


def test_changes_distinguish_new_forecasts_from_new_outcomes_of_old_forecasts(tmp_path: Path) -> None:
    first, second = tmp_path / "first.sqlite3", tmp_path / "second.sqlite3"
    _make_ledger(first)
    _disjoint_copy(first, second)
    with sqlite3.connect(second) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id='f-down-second'")
    a, b = build_scorecard(first), build_scorecard(second)
    combined = combine_scorecards([a, b])
    result = compare_scorecards(a, combined)
    assert len(result["new_forecast_ids"]) == 3
    assert len(result["new_forecast_horizon_rows"]) == 9
    assert result["newly_observed_existing_rows"] == []
    assert result["existing_observed_rows_unchanged"] == 9
    group = result["group_changes"][0]
    assert group["count_changes"]["directional_forecasts_scored"] == 1
    assert group["after"]["candidate_accuracy"] == 2 / 3
    assert group["candidate_accuracy_change_percentage_points"] == ((2 / 3) - .5) * 100.
    assert "улучшение модели этим не доказано" in render_changes_text(result)


@pytest.mark.parametrize("field", ["forecast_sha256", "capture_source_sha256", "capture_receipt_sha256", "engine_version", "created_at_t0"])
def test_changes_refuse_rehashed_changes_to_previous_forecast_origins(tmp_path: Path, field: str) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    before = build_scorecard(ledger)
    after = json.loads(json.dumps(before))
    for row in after["observations"]:
        if row["forecast_id"] == "f-up":
            row[field] = "2026-10-12T09:59:00Z" if field == "created_at_t0" else "OTHER_ENGINE" if field == "engine_version" else "a" * 64
    _resign_report(after)
    validate_scorecard(after)
    with pytest.raises(ScorecardRefused, match="Прежний прогноз"):
        compare_scorecards(before, after)


def test_changes_refuse_a_previous_direction_changed_even_with_consistent_scores(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    before = build_scorecard(ledger)
    after = json.loads(json.dumps(before))
    for row in after["observations"]:
        if row["forecast_id"] == "f-up":
            row["decision"] = "DOWN"
            row["candidate_correct"] = False
    for group in after["groups"]:
        group["candidate_correct"] = 0
        group["candidate_accuracy"] = 0.
        group["accuracy_difference_percentage_points"] = -100.
    _resign_report(after)
    validate_scorecard(after)
    with pytest.raises(ScorecardRefused, match="Прежний прогноз"):
        compare_scorecards(before, after)


@pytest.mark.parametrize("field", ["outcome_sha256", "future_source_sha256", "target_session", "target_session_end"])
def test_changes_refuse_modification_of_an_already_observed_outcome(tmp_path: Path, field: str) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    before = build_scorecard(ledger)
    after = json.loads(json.dumps(before))
    row = after["observations"][0]
    row[field] = "2026-10-19" if field == "target_session" else "2026-10-20T17:00:00Z" if field == "target_session_end" else "a" * 64
    _resign_report(after)
    validate_scorecard(after)
    with pytest.raises(ScorecardRefused, match="Прежний записанный исход"):
        compare_scorecards(before, after)


def test_changes_refuse_rollback_from_observed_to_pending(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    observed = build_scorecard(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes")
    pending = build_scorecard(ledger)
    with pytest.raises(ScorecardRefused, match="Прежний записанный исход"):
        compare_scorecards(observed, pending)


def test_changes_refuse_removal_of_a_previous_forecast(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    before = build_scorecard(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("DELETE FROM outcomes WHERE forecast_id='f-up'")
        db.execute("DELETE FROM captures WHERE forecast_id='f-up'")
    after = build_scorecard(ledger)
    with pytest.raises(ScorecardRefused, match="исчезли"):
        compare_scorecards(before, after)


def test_changes_cli_preserves_all_inputs_and_existing_output(tmp_path: Path) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    package = tmp_path / "package"
    build_package([ledger], package)
    output = tmp_path / "changes.json"
    assert changes_main(["--before", str(package), "--after", str(package), "--format", "json", "--output", str(output)]) == 0
    before = output.read_bytes()
    assert json.loads(before)["new_forecast_ids"] == []
    assert changes_main(["--before", str(package), "--after", str(package), "--output", str(output)]) == 2
    assert output.read_bytes() == before
    assert changes_main(["--before", str(package), "--after", str(package), "--output", str(package / "extra.txt")]) == 2
    verify_package(package)


def test_changes_refuse_a_package_changed_during_comparison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "pilot.sqlite3"
    _make_ledger(ledger)
    package = tmp_path / "package"
    build_package([ledger], package)
    original = changes_module.verify_package
    calls = [0]
    def changed(path: Path) -> dict[str, object]:
        calls[0] += 1
        result = original(path)
        if calls[0] == 3:
            result["manifest_sha256"] = "a" * 64
        return result
    monkeypatch.setattr(changes_module, "verify_package", changed)
    with pytest.raises(ScorecardRefused, match="изменился во время"):
        compare_packages(package, package)
