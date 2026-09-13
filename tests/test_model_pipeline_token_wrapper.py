from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_authorized_ydb_model_pipeline_token import (
    _inject_token_file,
    _read_token,
)


def test_pipeline_token_wrapper_injects_token_into_prepare(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("iam-token", encoding="utf-8")
    command = [
        "python",
        "/repo/scripts/run_authorized_ydb_prepare_validation.py",
        "--connection-string",
        "grpcs://example.invalid/db",
    ]

    updated = _inject_token_file(command, token_file=str(token_file))

    assert updated[:2] == command[:2]
    assert updated[-2:] == ["--token-file", str(token_file)]
    assert command[-1] == "grpcs://example.invalid/db"


def test_pipeline_token_wrapper_routes_validation_through_token_wrapper(
    tmp_path: Path,
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("iam-token", encoding="utf-8")
    command = [
        "python",
        "/repo/scripts/run_authorized_ydb_validation.py",
        "--connection-string",
        "grpcs://example.invalid/db",
        "--open-holdout",
    ]

    updated = _inject_token_file(command, token_file=str(token_file))

    assert Path(updated[1]).name == "run_authorized_ydb_validation_token.py"
    assert updated[-2:] == ["--token-file", str(token_file)]
    assert "--open-holdout" in updated


def test_pipeline_token_wrapper_does_not_duplicate_existing_token_file() -> None:
    command = [
        "python",
        "/repo/scripts/run_authorized_ydb_prepare_validation.py",
        "--token-file",
        "/tmp/token",
    ]

    updated = _inject_token_file(command, token_file="/tmp/token")

    assert updated.count("--token-file") == 1


def test_pipeline_token_wrapper_leaves_unrelated_commands_unchanged() -> None:
    command = ["python", "/repo/scripts/unrelated.py", "--flag"]
    assert _inject_token_file(command, token_file="/tmp/token") == command


def test_pipeline_token_wrapper_rejects_empty_token_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="token file is empty"):
        _read_token(str(token_file))
