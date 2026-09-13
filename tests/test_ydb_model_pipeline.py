from __future__ import annotations

import sys

import scripts.run_authorized_ydb_model_pipeline as pipeline


CALENDAR_FEASIBLE_TEST_ARGS = [
    "--validation-start",
    "2020-01-01",
    "--split-date",
    "2021-12-31",
    "--validation-end",
    "2023-12-31",
]

LEGACY_SHORT_PERIOD_ARGS = [
    "--validation-start",
    "2025-01-01",
    "--split-date",
    "2025-10-01",
    "--validation-end",
    "2026-05-31",
]

MODEL_FP = "model-fp"
DATA_FP = "data-fp"


def _argv(*extra: str) -> list[str]:
    return [
        "pipeline",
        "--connection-string",
        "grpcs://example.invalid/db",
        *extra,
    ]


def test_pipeline_preserves_d1_not_ready_reason(monkeypatch) -> None:
    calls: list[str] = []
    removed: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        calls.append(label)
        assert command[1].endswith("run_authorized_ydb_prepare_validation_runtime.py")
        return {"label": label, "returncode": 0, "status": "PASS"}

    def fake_read(path):
        assert path.endswith("ydb_validation_data_preparation.json")
        return {"status": "D1_NOT_READY", "readiness": {"status": "NOT_READY"}}

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_read", fake_read)
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: removed.append(path))
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(sys, "argv", _argv(*CALENDAR_FEASIBLE_TEST_ARGS))

    assert pipeline.main() == 2
    assert calls == ["prepare"]
    assert len(removed) == 1
    assert removed[0].endswith("ydb_validation_data_preparation.json")
    assert written[-1]["status"] == "D1_NOT_READY"
    assert written[-1]["validation"] == {"status": "NOT_RUN"}


def test_pipeline_preserves_d1_failure_even_when_prepare_exit_is_error(monkeypatch) -> None:
    calls: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        calls.append(label)
        return {"label": label, "returncode": 2, "status": "ERROR"}

    def fake_read(path):
        assert path.endswith("ydb_validation_data_preparation.json")
        return {
            "status": "D1_NOT_READY",
            "readiness": {
                "status": "NOT_EVALUATED",
                "reason": "D1 durable history preparation failed",
            },
            "price_operation_failures": [{"label": "price:GOLD:D1"}],
        }

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_read", fake_read)
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: None)
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(sys, "argv", _argv(*CALENDAR_FEASIBLE_TEST_ARGS))

    assert pipeline.main() == 2
    assert calls == ["prepare"]
    assert written[-1]["status"] == "D1_NOT_READY"
    assert written[-1]["price_operation_failures"] == [{"label": "price:GOLD:D1"}]
    assert written[-1]["validation"] == {"status": "NOT_RUN"}


def test_model_capacity_shortfall_is_reported_by_validation_not_data_preparation(monkeypatch) -> None:
    calls: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        calls.append(label)
        if label == "prepare":
            assert command[1].endswith("run_authorized_ydb_prepare_validation_runtime.py")
        else:
            assert command[1].endswith("run_authorized_ydb_validation_runtime.py")
        return {"label": label, "returncode": 0, "status": "PASS"}

    def fake_read(path):
        if path.endswith("ydb_validation_data_preparation.json"):
            return {
                "status": "READY",
                "persistent_price_timeframes": ["D1"],
                "intraday_mode": "ON_DEMAND_NOT_PERSISTED",
                "readiness": {"status": "READY"},
            }
        assert path.endswith("ydb_model_validation.json")
        return {
            "run_status": "COMPUTED",
            "model_status": "INSUFFICIENT_DATA",
            "capacity_shortfall": {"development": {"BR": {"20": 0}}},
            "holdout_evaluated": False,
            "holdout_sealed": True,
        }

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_read", fake_read)
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: None)
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(sys, "argv", _argv(*CALENDAR_FEASIBLE_TEST_ARGS))

    assert pipeline.main() == 0
    assert calls == ["prepare", "validate"]
    result = written[-1]
    assert result["status"] == "COMPUTED"
    assert result["model_status"] == "INSUFFICIENT_DATA"
    assert result["capacity_shortfall"]["development"]["BR"]["20"] == 0
    assert result["persistent_price_timeframes"] == ["D1"]
    assert result["intraday_mode"] == "ON_DEMAND_NOT_PERSISTED"


def test_final_pipeline_keeps_rejected_model_as_valid_computed_result(monkeypatch) -> None:
    calls: list[str] = []
    removed: list[str] = []
    written: list[dict[str, object]] = []
    commands: list[list[str]] = []

    def fake_run(command, *, label):
        calls.append(label)
        commands.append(list(command))
        return {"label": label, "returncode": 0, "status": "PASS"}

    def fake_read(path):
        assert path.endswith("ydb_model_validation.json")
        return {
            "run_status": "COMPUTED",
            "model_status": "REJECTED",
            "selected_parameters": {"name": "baseline"},
            "selected_model_fingerprint": MODEL_FP,
            "selected_data_fingerprint": DATA_FP,
            "development_statuses": {"SBER": "ACCEPTED"},
            "holdout_evaluated": True,
            "holdout_sealed": False,
            "holdout_statuses": {"SBER": "REJECTED"},
        }

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_read", fake_read)
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: removed.append(path))
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(
        sys,
        "argv",
        _argv(
            *CALENDAR_FEASIBLE_TEST_ARGS,
            "--open-holdout",
            "--expected-model-fingerprint",
            MODEL_FP,
            "--expected-data-fingerprint",
            DATA_FP,
        ),
    )

    assert pipeline.main() == 0
    assert calls == ["validate"]
    assert len(removed) == 1
    assert removed[0].endswith("ydb_model_validation.json")
    assert commands[0][1].endswith("run_authorized_ydb_validation_runtime.py")
    assert "--open-holdout" in commands[0]
    assert written[-1]["status"] == "COMPUTED"
    assert written[-1]["model_status"] == "REJECTED"
    assert written[-1]["holdout_evaluated"] is True


def test_pipeline_stops_before_prepare_when_dates_cannot_support_required_sample(monkeypatch) -> None:
    calls: list[str] = []
    removed: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        calls.append(label)
        raise AssertionError("external stage must not run for impossible statistics")

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: removed.append(path))
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(sys, "argv", _argv(*LEGACY_SHORT_PERIOD_ARGS))

    assert pipeline.main() == 0
    assert calls == []
    assert removed == []
    result = written[-1]
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["model_status"] == "INSUFFICIENT_DATA"
    assert result["prepare"] == {"status": "NOT_RUN"}
    assert result["validation"] == {"status": "NOT_RUN"}
    assert result["holdout_evaluated"] is False
    assert result["capacity_shortfall_upper_bound"]["development"]["20"] < 20
    assert result["capacity_shortfall_upper_bound"]["holdout"]["20"] < 20


def test_governed_defaults_are_not_the_consumed_legacy_window() -> None:
    assert pipeline.VALIDATION_PROTOCOL == "M23_HISTORICAL_GOVERNED_V1"
    assert pipeline.DEFAULT_VALIDATION_START == "2021-01-01"
    assert pipeline.DEFAULT_SPLIT_DATE == "2022-12-31"
    assert pipeline.DEFAULT_VALIDATION_END == "2024-12-31"
