from __future__ import annotations

import sys

import scripts.run_authorized_ydb_model_pipeline as pipeline


ENGINE = "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS"
MODEL_FINGERPRINT = "sealed-development-model-fingerprint"
DATA_FINGERPRINT = "sealed-development-data-fingerprint"


def _open_args() -> list[str]:
    return [
        "pipeline",
        "--connection-string",
        "grpcs://example.invalid/db",
        "--open-holdout",
        "--expected-model-fingerprint",
        MODEL_FINGERPRINT,
        "--expected-data-fingerprint",
        DATA_FINGERPRINT,
    ]


def test_default_pipeline_keeps_holdout_sealed(monkeypatch) -> None:
    commands: list[tuple[str, list[str]]] = []
    removed: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        commands.append((label, list(command)))
        return {"label": label, "returncode": 0, "status": "PASS"}

    def fake_read(path):
        if path.endswith("ydb_validation_data_preparation.json"):
            return {"status": "READY", "readiness": {"status": "READY"}}
        if path.endswith("ydb_model_validation.json"):
            return {
                "run_status": "COMPUTED",
                "model_status": "DEVELOPMENT_ACCEPTED_HOLDOUT_SEALED",
                "selected_parameters": {"name": "baseline"},
                "selected_model_fingerprint": MODEL_FINGERPRINT,
                "selected_data_fingerprint": DATA_FINGERPRINT,
                "dataset_fingerprint": {
                    "sha256": DATA_FINGERPRINT,
                    "contract_sessions": 1000,
                    "price_rows": 50000,
                    "flow_rows": 10000,
                },
                "development_statuses": {"SBER": "ACCEPTED"},
                "holdout_evaluated": False,
                "holdout_sealed": True,
                "holdout_statuses": {},
                "holdout_claim": None,
            }
        raise AssertionError(path)

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_read", fake_read)
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: removed.append(path))
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline",
            "--connection-string",
            "grpcs://example.invalid/db",
            "--validation-start",
            "2021-01-01",
            "--split-date",
            "2022-12-31",
            "--validation-end",
            "2024-12-31",
        ],
    )

    assert pipeline.main() == 0
    assert [label for label, _ in commands] == ["prepare", "validate"]
    validate_command = commands[-1][1]
    assert "--open-holdout" not in validate_command
    assert "--expected-model-fingerprint" not in validate_command
    assert "--expected-data-fingerprint" not in validate_command
    assert len(removed) == 2
    result = written[-1]
    assert result["status"] == "DEVELOPMENT_ACCEPTED_HOLDOUT_SEALED"
    assert result["selected_model_fingerprint"] == MODEL_FINGERPRINT
    assert result["selected_data_fingerprint"] == DATA_FINGERPRINT
    assert result["holdout_evaluated"] is False
    assert result["holdout_sealed"] is True


def test_pipeline_passes_both_sealed_fingerprints_to_final_validation(monkeypatch) -> None:
    commands: list[tuple[str, list[str]]] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        commands.append((label, list(command)))
        return {"label": label, "returncode": 0, "status": "PASS"}

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(
        pipeline,
        "_read",
        lambda path: {
            "run_status": "COMPUTED",
            "model_status": "REJECTED",
            "selected_model_fingerprint": MODEL_FINGERPRINT,
            "selected_data_fingerprint": DATA_FINGERPRINT,
            "holdout_evaluated": True,
            "holdout_sealed": False,
            "holdout_statuses": {"SBER": "REJECTED"},
            "holdout_claim": {
                "model_fingerprint": MODEL_FINGERPRINT,
                "data_fingerprint": DATA_FINGERPRINT,
            },
        },
    )
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: None)
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(sys, "argv", _open_args())

    assert pipeline.main() == 0
    assert [label for label, _ in commands] == ["validate"]
    command = commands[0][1]
    assert "--open-holdout" in command
    model_index = command.index("--expected-model-fingerprint")
    data_index = command.index("--expected-data-fingerprint")
    assert command[model_index + 1] == MODEL_FINGERPRINT
    assert command[data_index + 1] == DATA_FINGERPRINT
    assert written[-1]["status"] == "COMPUTED"


def test_pipeline_preserves_holdout_already_consumed(monkeypatch) -> None:
    commands: list[tuple[str, list[str]]] = []
    removed: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        commands.append((label, list(command)))
        return {"label": label, "returncode": 0, "status": "PASS"}

    def fake_read(path):
        assert path.endswith("ydb_model_validation.json")
        return {
            "run_status": "HOLDOUT_ALREADY_CONSUMED",
            "model_status": "NOT_EVALUATED",
            "holdout_evaluated": False,
            "holdout_statuses": {},
            "holdout_claim": {
                "holdout_start": "2023-01-01",
                "holdout_end": "2024-12-31",
                "protocol": "M23_HISTORICAL_GOVERNED_V1",
                "engine_version": ENGINE,
                "model_fingerprint": MODEL_FINGERPRINT,
                "data_fingerprint": DATA_FINGERPRINT,
                "consumed_at": "2026-09-11T00:00:00+00:00",
            },
        }

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_read", fake_read)
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: removed.append(path))
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(sys, "argv", _open_args())

    assert pipeline.main() == 0
    assert [label for label, _ in commands] == ["validate"]
    assert len(removed) == 1
    result = written[-1]
    assert result["status"] == "HOLDOUT_ALREADY_CONSUMED"
    assert result["model_status"] == "NOT_EVALUATED"
    assert result["holdout_evaluated"] is False
    assert result["holdout_claim"]["data_fingerprint"] == DATA_FINGERPRINT


def test_pipeline_preserves_model_fingerprint_mismatch(monkeypatch) -> None:
    written: list[dict[str, object]] = []

    monkeypatch.setattr(
        pipeline,
        "_run",
        lambda command, *, label: {"label": label, "returncode": 0, "status": "PASS"},
    )
    monkeypatch.setattr(
        pipeline,
        "_read",
        lambda path: {
            "run_status": "MODEL_FINGERPRINT_MISMATCH",
            "model_status": "NOT_EVALUATED",
            "expected_model_fingerprint": MODEL_FINGERPRINT,
            "actual_model_fingerprint": "changed-model",
            "selected_data_fingerprint": DATA_FINGERPRINT,
            "holdout_evaluated": False,
            "holdout_claim": None,
        },
    )
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: None)
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(sys, "argv", _open_args())

    assert pipeline.main() == 0
    result = written[-1]
    assert result["status"] == "MODEL_FINGERPRINT_MISMATCH"
    assert result["holdout_evaluated"] is False
    assert result["actual_model_fingerprint"] == "changed-model"


def test_pipeline_preserves_data_fingerprint_mismatch(monkeypatch) -> None:
    written: list[dict[str, object]] = []

    monkeypatch.setattr(
        pipeline,
        "_run",
        lambda command, *, label: {"label": label, "returncode": 0, "status": "PASS"},
    )
    monkeypatch.setattr(
        pipeline,
        "_read",
        lambda path: {
            "run_status": "DATA_FINGERPRINT_MISMATCH",
            "model_status": "NOT_EVALUATED",
            "expected_data_fingerprint": DATA_FINGERPRINT,
            "actual_data_fingerprint": "changed-data",
            "holdout_evaluated": False,
            "holdout_sealed": True,
            "holdout_claim": None,
        },
    )
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: None)
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(sys, "argv", _open_args())

    assert pipeline.main() == 0
    result = written[-1]
    assert result["status"] == "DATA_FINGERPRINT_MISMATCH"
    assert result["model_status"] == "NOT_EVALUATED"
    assert result["holdout_evaluated"] is False
    assert result["holdout_sealed"] is True
    assert result["actual_data_fingerprint"] == "changed-data"
