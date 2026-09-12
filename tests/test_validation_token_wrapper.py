from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_authorized_ydb_validation_token.py"
    spec = importlib.util.spec_from_file_location("validation_token_wrapper_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_token_strips_whitespace(tmp_path: Path) -> None:
    module = _module()
    token_file = tmp_path / "token"
    token_file.write_text("  short-lived-token\n", encoding="utf-8")
    assert module._read_token(str(token_file)) == "short-lived-token"


def test_read_token_rejects_empty_file(tmp_path: Path) -> None:
    module = _module()
    token_file = tmp_path / "token"
    token_file.write_text("\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="token file is empty"):
        module._read_token(str(token_file))
