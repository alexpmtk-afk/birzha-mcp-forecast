from __future__ import annotations

import sys

import scripts.run_authorized_ydb_model_pipeline as pipeline


def test_pipeline_does_not_validate_when_readiness_is_not_ready(monkeypatch) -> None:
    calls: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        calls.append(label)
        return {"label": label, "returncode": 0, "status": "PASS"}

    def fake_read(path):
        assert path.endswith("ydb_validation_data_preparation.json")
        return {"status": "DATA_NOT_READY", "readiness": {"status": "NOT_READY"}}

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_read", fake_read)
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(
        sys,
        "argv",
        ["pipeline", "--connection-string", "grpcs://example.invalid/db"],
    )

    assert pipeline.main() == 2
    assert calls == ["prepare"]
    assert written[-1]["status"] == "PREPARE_NOT_READY"
    assert written[-1]["validation"] == {"status": "NOT_RUN"}


def test_pipeline_keeps_rejected_model_as_valid_computed_result(monkeypatch) -> None:
    calls: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        calls.append(label)
        return {"label": label, "returncode": 0, "status": "PASS"}

    def fake_read(path):
        if path.endswith("ydb_validation_data_preparation.json"):
            return {"status": "READY", "readiness": {"status": "READY"}}
        if path.endswith("ydb_model_validation.json"):
            return {
                "run_status": "COMPUTED",
                "model_status": "REJECTED",
                "selected_parameters": {"name": "baseline"},
                "holdout_statuses": {"SBER": "REJECTED"},
            }
        raise AssertionError(path)

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_read", fake_read)
    monkeypatch.setattr(pipeline, "_write", lambda path, payload: written.append(payload.copy()))
    monkeypatch.setattr(
        sys,
        "argv",
        ["pipeline", "--connection-string", "grpcs://example.invalid/db"],
    )

    assert pipeline.main() == 0
    assert calls == ["prepare", "validate"]
    assert written[-1]["status"] == "COMPUTED"
    assert written[-1]["model_status"] == "REJECTED"
