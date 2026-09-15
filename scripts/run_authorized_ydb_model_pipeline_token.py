from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def _read_token(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("token file is empty")
    return value


def _inject_token_file(command: list[str], *, token_file: str) -> list[str]:
    if len(command) < 2:
        return list(command)
    target = Path(command[1]).name
    updated = list(command)
    if target in {
        "run_authorized_ydb_prepare_validation.py",
        "run_authorized_ydb_prepare_validation_runtime.py",
    }:
        if "--token-file" not in updated:
            updated.extend(["--token-file", token_file])
        return updated
    if target in {
        "run_authorized_ydb_validation.py",
        "run_authorized_ydb_validation_runtime.py",
    }:
        updated[1] = str(Path(command[1]).with_name("run_authorized_ydb_validation_token.py"))
        if "--token-file" not in updated:
            updated.extend(["--token-file", token_file])
        return updated
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(
        add_help=False,
        description=(
            "Inject an externally minted Yandex IAM token into the governed "
            "D1-only YDB model pipeline without changing model/holdout semantics."
        ),
    )
    parser.add_argument("--token-file", required=True)
    args, remaining = parser.parse_known_args()
    _read_token(args.token_file)

    implementation = Path(__file__).with_name("run_authorized_ydb_model_pipeline.py")
    spec = importlib.util.spec_from_file_location(
        "birzha_governed_ydb_model_pipeline_impl", implementation
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load governed model pipeline implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    original_run = module._run

    def run_with_token(command: list[str], *, label: str) -> dict[str, object]:
        return original_run(
            _inject_token_file(command, token_file=args.token_file),
            label=label,
        )

    module._run = run_with_token

    original_argv = sys.argv
    try:
        sys.argv = [str(implementation), *remaining]
        return int(module.main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
