from __future__ import annotations

from dataclasses import replace

import pytest

from birzha.domain.forecast import ForecastRecord, HorizonForecast
from birzha.storage.forecast_journal import DuckDBForecastJournal, ForecastCollisionError


def _record() -> ForecastRecord:
    return ForecastRecord(
        forecast_id="fcst_test_001",
        symbol="Si",
        secid="SiU6",
        created_at_t0="2026-08-28T13:00:00+03:00",
        engine_version="BIRZHA_FORECAST_BASELINE_V0_2_FLOW",
        direction="UP",
        signal_strength=0.6,
        control="BUYERS",
        route="TREND",
        horizons=(
            HorizonForecast(5, "UP", 0.6, 2.0, 1.0),
            HorizonForecast(10, "UP", 0.6, 3.0, 1.5),
            HorizonForecast(20, "UP", 0.6, 4.0, 2.0),
        ),
        reasons=("reason",),
        warnings=("baseline_engine_not_probability_calibrated",),
        validation_status="UNVALIDATED_BASELINE",
    )


def test_append_then_identical_duplicate_is_idempotent() -> None:
    journal = DuckDBForecastJournal(":memory:")
    record = _record()

    first = journal.append(record)
    second = journal.append(record)

    assert first.status == "APPENDED"
    assert second.status == "DUPLICATE_IDENTICAL"
    assert first.payload_hash == second.payload_hash
    assert journal.count() == 1
    assert journal.get(record.forecast_id) == record


def test_same_identity_with_modified_payload_is_rejected() -> None:
    journal = DuckDBForecastJournal(":memory:")
    record = _record()
    journal.append(record)

    conflicting = replace(record, direction="DOWN", control="SELLERS")
    with pytest.raises(ForecastCollisionError):
        journal.append(conflicting)

    assert journal.count() == 1
    assert journal.get(record.forecast_id) == record


def test_file_backed_journal_survives_reopen(tmp_path) -> None:
    path = tmp_path / "forecast.duckdb"
    record = _record()

    first = DuckDBForecastJournal(str(path))
    first.append(record)
    first.close()

    reopened = DuckDBForecastJournal(str(path))
    assert reopened.storage_scope == "local_file"
    assert reopened.get(record.forecast_id) == record
    assert reopened.list_recent(symbol="si") == [record]
    reopened.close()


def test_canonical_hash_changes_when_immutable_payload_changes() -> None:
    record = _record()
    _, first_hash = DuckDBForecastJournal.canonical_payload(record)
    _, second_hash = DuckDBForecastJournal.canonical_payload(replace(record, signal_strength=0.61))
    assert first_hash != second_hash
