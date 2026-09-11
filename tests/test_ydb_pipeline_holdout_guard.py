from __future__ import annotations

import sys

import scripts.run_authorized_ydb_model_pipeline as pipeline


ENGINE = "BIRZHA_FORECAST_BASELINE_V0_4_SCENARIOS"


def test_pipeline_preserves_holdout_already_consumed(monkeypatch) -> None:
    calls: list[str] = []
    removed: list[str] = []
    written: list[dict[str, object]] = []

    def fake_run(command, *, label):
        calls.append(label)
        return {"label": label, "returncode": 0, "status": "PASS"}

    def fake_read(path):
        if path.endswith("ydb_validation_data_preparation.json"):
            return {"status": "READY", "readiness": {"status": "READY"}}
        if path.endswith("ydb_model_validation.json"):
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
                    "model_fingerprint": "abc",
                    "consumed_at": "2026-09-11T00:00:00+00:00",
                },
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
    assert calls == ["prepare", "validate"]
    assert len(removed) == 2
    result = written[-1]
    assert result["status"] == "HOLDOUT_ALREADY_CONSUMED"
    assert result["model_status"] == "NOT_EVALUATED"
    assert result["holdout_evaluated"] is False
    assert result["holdout_claim"]["holdout_start"] == "2023-01-01"
    assert result["holdout_claim"]["engine_version"] == ENGINE
