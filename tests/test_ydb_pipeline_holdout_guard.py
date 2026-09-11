from __future__ import annotations

import sys

import scripts.run_authorized_ydb_model_pipeline as pipeline


ENGINE = "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS"
FINGERPRINT = "sealed-development-fingerprint"


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
                "selected_model_fingerprint": FINGERPRINT,
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
    assert len(removed) == 2
    result = written[-1]
    assert result["status"] == "DEVELOPMENT_ACCEPTED_HOLDOUT_SEALED"
    assert result["selected_model_fingerprint"] == FINGERPRINT
    assert result["holdout_evaluated"] is False
    assert result["holdout_sealed"] is True


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
                "model_fingerprint": FINGERPRINT,
                "consumed_at": "2026-09-11T00:00:00+00:00",
            },
        }

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
            "--open-holdout",
            "--expected-model-fingerprint",
            FINGERPRINT,
        ],
    )

    assert pipeline.main() == 0
    assert [label for label, _ in commands] == ["validate"]
    validate_command = commands[0][1]
    assert "--open-holdout" in validate_command
    index = validate_command.index("--expected-model-fingerprint")
    assert validate_command[index + 1] == FINGERPRINT
    assert len(removed) == 1
    result = written[-1]
    assert result["status"] == "HOLDOUT_ALREADY_CONSUMED"
    assert result["model_status"] == "NOT_EVALUATED"
    assert result["holdout_evaluated"] is False
    assert result["holdout_claim"]["holdout_start"] == "2023-01-01"
    assert result["holdout_claim"]["engine_version"] == ENGINE


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
            "expected_model_fingerprint": FINGERPRINT,
            "actual_model_fingerprint": "changed-model",
            "holdout_evaluated": False,
            "holdout_claim": None,
        },
    )
    monkeypatch.setattr(pipeline, "_remove_existing", lambda path: None)
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline",
            "--connection-string",
            "grpcs://example.invalid/db",
            "--open-holdout",
            "--expected-model-fingerprint",
            FINGERPRINT,
        ],
    )

    assert pipeline.main() == 0
    result = written[-1]
    assert result["status"] == "MODEL_FINGERPRINT_MISMATCH"
    assert result["holdout_evaluated"] is False
    assert result["actual_model_fingerprint"] == "changed-model"
