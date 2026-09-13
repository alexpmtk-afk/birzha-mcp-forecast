from __future__ import annotations

import ast
from pathlib import Path


WORKER_SOURCE = Path(__file__).resolve().parents[1] / "src" / "birzha" / "worker.py"


def _tree() -> ast.Module:
    return ast.parse(WORKER_SOURCE.read_text(encoding="utf-8"))


def test_worker_module_has_executable_http_server_entrypoint() -> None:
    tree = _tree()

    main_functions = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    assert len(main_functions) == 1

    main = main_functions[0]
    uvicorn_calls = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
        and node.func.attr == "run"
    ]
    assert len(uvicorn_calls) == 1

    call = uvicorn_calls[0]
    assert call.args and isinstance(call.args[0], ast.Name) and call.args[0].id == "app"
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}
    assert isinstance(keywords.get("host"), ast.Attribute)
    assert isinstance(keywords.get("port"), ast.Attribute)
    assert ast.unparse(keywords["host"]) == "settings.host"
    assert ast.unparse(keywords["port"]) == "settings.port"


def test_worker_module_invokes_main_when_executed_as_module() -> None:
    tree = _tree()

    guards = []
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        try:
            condition = ast.unparse(node.test)
        except Exception:
            continue
        if condition == "__name__ == '__main__'":
            guards.append(node)

    assert len(guards) == 1
    calls_main = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "main"
        for node in ast.walk(guards[0])
    )
    assert calls_main
