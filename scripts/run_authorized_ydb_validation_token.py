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


def main() -> int:
    parser = argparse.ArgumentParser(
        add_help=False,
        description="Inject an externally minted Yandex IAM token into governed YDB validation",
    )
    parser.add_argument("--token-file", required=True)
    args, remaining = parser.parse_known_args()
    token = _read_token(args.token_file)

    implementation = Path(__file__).with_name("run_authorized_ydb_validation.py")
    spec = importlib.util.spec_from_file_location(
        "birzha_governed_ydb_validation_impl", implementation
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load governed validation implementation")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # The implementation keeps the credential acquisition behind _token().
    # Replace only that boundary; all validation, fingerprint and one-shot
    # holdout semantics remain in the canonical implementation unchanged.
    module._token = lambda: token

    original_argv = sys.argv
    try:
        sys.argv = [str(implementation), *remaining]
        return int(module.main())
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
