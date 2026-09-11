from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


DEFAULT_VALIDATION_START = "2025-01-01"
DEFAULT_SPLIT_DATE = "2025-10-01"
DEFAULT_VALIDATION_END = "2026-05-31"


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
    parser.add_argument("--max-points", type=int, default=24)
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
    if args.step_sessions <= 0:
        raise ValueError("step_sessions must be > 0")
    if args.max_points <= 0:
        raise ValueError("max_points must be > 0")

    python = sys.executable
    prepare = _run(
        [
            python,
            "scripts/run_authorized_ydb_prepare_validation.py",
            "--connection-string",
            args.connection_string,
            "--validation-start",
            args.validation_start,
            "--validation-end",
            args.validation_end,
            "--artifact",
            args.prepare_artifact,
        ],
        label="prepare",
    )

    artifact: dict[str, object] = {
        "validation_start": args.validation_start,
        "split_date": args.split_date,
        "validation_end": args.validation_end,
        "step_sessions": args.step_sessions,
        "max_points": args.max_points,
        "prepare": prepare,
        "prepare_artifact": args.prepare_artifact,
        "validation_artifact": args.validation_artifact,
    }
    if prepare["status"] != "PASS":
        artifact["status"] = "PREPARE_FAILED"
        artifact["validation"] = {"status": "NOT_RUN"}
        _write(args.pipeline_artifact, artifact)
        print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
        return 2

    validation = _run(
        [
            python,
            "scripts/run_authorized_ydb_validation.py",
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
    artifact["status"] = "COMPUTED" if validation["status"] == "PASS" else "VALIDATION_FAILED"
    _write(args.pipeline_artifact, artifact)
    print(json.dumps(artifact, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if validation["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
