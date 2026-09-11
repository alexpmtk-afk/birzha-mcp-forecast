from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from birzha.application.validation import independent_sample_capacity


DEFAULT_VALIDATION_START = "2025-01-01"
DEFAULT_SPLIT_DATE = "2025-10-01"
DEFAULT_VALIDATION_END = "2026-05-31"
DEFAULT_MAX_POINTS = 80
MINIMUM_ACCEPTANCE_OBSERVATIONS = 20


def _run(command: list[str], *, label: str) -> dict[str, object]:
    completed = subprocess.run(command, check=False)
    return {
        "label": label,
        "returncode": int(completed.returncode),
        "status": "PASS" if completed.returncode == 0 else "ERROR",
    }


def _write(path: str, payload: dict[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _remove_existing(path: str) -> None:
    target = Path(path)
    if target.exists():
        if not target.is_file():
            raise RuntimeError(f"artifact path exists but is not a file: {path}")
        target.unlink()


def _read(path: str) -> dict[str, object]:
    target = Path(path)
    if not target.is_file():
        raise RuntimeError(f"required artifact was not created: {path}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"artifact must contain a JSON object: {path}")
    return payload


def _calendar_capacity_upper_bound(
    start_date: str,
    end_date: str,
    *,
    step_sessions: int,
    max_points: int,
) -> dict[int, int]:
    start = date.fromisoformat(start_date[:10])
    end = date.fromisoformat(end_date[:10])
    if end < start:
        raise ValueError("capacity period end must not be before start")
    calendar_days = (end - start).days + 1
    return independent_sample_capacity(
        calendar_days,
        step_sessions=step_sessions,
        max_points=max_points,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare authorized YDB history and then run the six-market "
            "development/holdout validation only if preparation succeeds"
        )
    )
    parser.add_argument("--connection-string", required=True)
    parser.add_argument("--validation-start", default=DEFAULT_VALIDATION_START)
    parser.add_argument("--split-date", default=DEFAULT_SPLIT_DATE)
    parser.add_argument("--validation-end", default=DEFAULT_VALIDATION_END)
    parser.add_argument("--step-sessions", type=int, default=5)
    parser.add_argument("--max-points", type=int, default=DEFAULT_MAX_POINTS)
    parser.add_argument(
        "--prepare-artifact",
        default="artifacts/ydb_validation_data_preparation.json",
    )
    parser.add_argument(
        "--validation-artifact",
        default="artifacts/ydb_model_validation.json",
    )
    parser.add_argument(
        "--pipeline-artifact",
        default="artifacts/ydb_model_pipeline.json",
    )
    args = parser.parse_args()

    if not args.validation_start < args.split_date < args.validation_end:
        raise ValueError("expected validation_start < split_date < validation_end")
    if args.step_sessions <= 0 or args.step_sessions > 50:
        raise ValueError("step_sessions must be between 1 and 50")
    if args.max_points <= 0 or args.max_points > 240:
        raise ValueError("max_points must be between 1 and 240")

    holdout_start = (
        date.fromisoformat(args.split_date[:10]) + timedelta(days=1)
    ).isoformat()
    development_upper = _calendar_capacity_upper_bound(
        args.validation_start,
        args.split_date,
        step_sessions=args.step_sessions,
        max_points=args.max_points,
    )
    holdout_upper = _calendar_capacity_upper_bound(
        holdout_start,
        args.validation_end,
        step_sessions=args.step_sessions,
        max_points=args.max_points,
    )
    capacity_upper_bound = {
        "development": {str(k): v for k, v in development_upper.items()},
        "holdout": {str(k): v for k, v in holdout_upper.items()},
    }
    impossible = {
        period: {
            horizon: count
            for horizon, count in values.items()
            if count < MINIMUM_ACCEPTANCE_OBSERVATIONS
        }
        for period, values in capacity_upper_bound.items()
    }
    impossible = {period: values for period, values in impossible.items() if values}

    artifact: dict[str, object] = {
        "validation_start": args.validation_start,
        "split_date": args.split_date,
        "validation_end": args.validation_end,
        "step_sessions": args.step_sessions,
        "max_points": args.max_points,
        "minimum_acceptance_observations": MINIMUM_ACCEPTANCE_OBSERVATIONS,
        "calendar_capacity_upper_bound": capacity_upper_bound,
        "prepare_artifact": args.prepare_artifact,
        "validation_artifact": args.validation_artifact,
    }
    if impossible:
        artifact["status"] = "INSUFFICIENT_DATA"
        artifact["model_status"] = "INSUFFICIENT_DATA"
        artifact["capacity_shortfall_upper_bound"] = impossible
        artifact["prepare"] = {"status": "NOT_RUN"}
        artifact["validation"] = {"status": "NOT_RUN"}
        artifact["holdout_evaluated"] = False
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 0

    python = sys.executable
    scripts_dir = Path(__file__).resolve().parent
    _remove_existing(args.prepare_artifact)
    prepare = _run(
        [
            python,
            str(scripts_dir / "run_authorized_ydb_prepare_validation.py"),
            "--connection-string",
            args.connection_string,
            "--validation-start",
            args.validation_start,
            "--split-date",
            args.split_date,
            "--validation-end",
            args.validation_end,
            "--step-sessions",
            str(args.step_sessions),
            "--max-points",
            str(args.max_points),
            "--artifact",
            args.prepare_artifact,
        ],
        label="prepare",
    )
    artifact["prepare"] = prepare
    if prepare["status"] != "PASS":
        artifact["status"] = "PREPARE_FAILED"
        artifact["validation"] = {"status": "NOT_RUN"}
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 2

    try:
        prepare_evidence = _read(args.prepare_artifact)
    except Exception as exc:
        artifact["status"] = "PREPARE_EVIDENCE_INVALID"
        artifact["prepare_evidence_error"] = f"{type(exc).__name__}:{exc}"
        artifact["validation"] = {"status": "NOT_RUN"}
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 2

    prepare_evidence_status = prepare_evidence.get("status")
    artifact["prepare_evidence_status"] = prepare_evidence_status
    artifact["session_capacity"] = prepare_evidence.get("session_capacity")
    artifact["capacity_shortfall"] = prepare_evidence.get("capacity_shortfall")
    if prepare_evidence_status == "INSUFFICIENT_DATA":
        artifact["status"] = "INSUFFICIENT_DATA"
        artifact["model_status"] = "INSUFFICIENT_DATA"
        artifact["validation"] = {"status": "NOT_RUN"}
        artifact["holdout_evaluated"] = False
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 0

    readiness = prepare_evidence.get("readiness")
    readiness_status = readiness.get("status") if isinstance(readiness, dict) else None
    artifact["readiness_status"] = readiness_status
    if readiness_status != "READY":
        artifact["status"] = "PREPARE_NOT_READY"
        artifact["validation"] = {"status": "NOT_RUN"}
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 2

    _remove_existing(args.validation_artifact)
    validation = _run(
        [
            python,
            str(scripts_dir / "run_authorized_ydb_validation.py"),
            "--connection-string",
            args.connection_string,
            "--development-start",
            args.validation_start,
            "--split-date",
            args.split_date,
            "--holdout-end",
            args.validation_end,
            "--step-sessions",
            str(args.step_sessions),
            "--max-points",
            str(args.max_points),
            "--artifact",
            args.validation_artifact,
        ],
        label="validate",
    )
    artifact["validation"] = validation
    if validation["status"] != "PASS":
        artifact["status"] = "VALIDATION_FAILED"
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 3

    try:
        validation_evidence = _read(args.validation_artifact)
    except Exception as exc:
        artifact["status"] = "VALIDATION_EVIDENCE_INVALID"
        artifact["validation_evidence_error"] = f"{type(exc).__name__}:{exc}"
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 3

    run_status = validation_evidence.get("run_status")
    model_status = validation_evidence.get("model_status")
    artifact["validation_run_status"] = run_status
    artifact["model_status"] = model_status
    artifact["selected_parameters"] = validation_evidence.get("selected_parameters")
    artifact["development_statuses"] = validation_evidence.get("development_statuses")
    artifact["development_capacity"] = validation_evidence.get("development_capacity")
    artifact["holdout_capacity"] = validation_evidence.get("holdout_capacity")
    artifact["capacity_shortfall"] = validation_evidence.get("capacity_shortfall")
    artifact["holdout_evaluated"] = validation_evidence.get("holdout_evaluated")
    artifact["holdout_statuses"] = validation_evidence.get("holdout_statuses")
    if run_status != "COMPUTED" or not isinstance(model_status, str):
        artifact["status"] = "VALIDATION_EVIDENCE_INVALID"
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 3

    artifact["status"] = "COMPUTED"
    _write(args.pipeline_artifact, artifact)
    print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
