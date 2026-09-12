"""Fail-closed repository integrity checks for BIRZHA MCP Forecast System."""

from __future__ import annotations

from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    "pyproject.toml",
    "Dockerfile",
    "README.md",
    "src/birzha/__init__.py",
    "src/birzha/main.py",
    "src/birzha/mcp/server.py",
    "src/birzha/application/market_data.py",
    "src/birzha/application/historical_data.py",
    "src/birzha/application/snapshot.py",
    "src/birzha/application/forecast.py",
    "src/birzha/application/validation.py",
)

REQUIRED_DIRS = (
    "src/birzha",
    "tests",
    "scripts",
)

REQUIRED_RUNTIME_DEPENDENCIES = {
    "httpx==0.28.1",
    "duckdb==1.5.5",
    "mcp==2.1.1",
    "uvicorn==0.35.0",
    "ydb[yc]==3.31.4",
    "tzdata==2026.3",
}

MIN_SOURCE_PY_FILES = 35
MIN_TEST_PY_FILES = 20
MIN_SCRIPT_PY_FILES = 3


def _failures() -> list[str]:
    errors: list[str] = []

    for relative in REQUIRED_FILES:
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"required file missing: {relative}")

    for relative in REQUIRED_DIRS:
        path = ROOT / relative
        if not path.is_dir():
            errors.append(f"required directory missing: {relative}")
        elif not any(path.iterdir()):
            errors.append(f"required directory empty: {relative}")

    source_count = len(tuple((ROOT / "src/birzha").rglob("*.py")))
    test_count = len(tuple((ROOT / "tests").rglob("test_*.py")))
    script_count = len(tuple((ROOT / "scripts").rglob("*.py")))

    if source_count < MIN_SOURCE_PY_FILES:
        errors.append(
            f"source tree unexpectedly small: {source_count} < {MIN_SOURCE_PY_FILES} Python files"
        )
    if test_count < MIN_TEST_PY_FILES:
        errors.append(
            f"test tree unexpectedly small: {test_count} < {MIN_TEST_PY_FILES} test files"
        )
    if script_count < MIN_SCRIPT_PY_FILES:
        errors.append(
            f"scripts tree unexpectedly small: {script_count} < {MIN_SCRIPT_PY_FILES} Python files"
        )

    pyproject_path = ROOT / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            with pyproject_path.open("rb") as handle:
                config = tomllib.load(handle)
        except Exception as exc:  # pragma: no cover - fail-closed diagnostics
            errors.append(f"pyproject.toml cannot be parsed: {exc}")
        else:
            project = config.get("project", {})
            dependencies = set(project.get("dependencies", ()))
            missing_dependencies = sorted(REQUIRED_RUNTIME_DEPENDENCIES - dependencies)
            if missing_dependencies:
                errors.append(
                    "required runtime dependencies missing: " + ", ".join(missing_dependencies)
                )

            packages = (
                config.get("tool", {})
                .get("hatch", {})
                .get("build", {})
                .get("targets", {})
                .get("wheel", {})
                .get("packages", ())
            )
            if "src/birzha" not in packages:
                errors.append("wheel package path missing: src/birzha")

            requires_python = str(project.get("requires-python", ""))
            if ">=3.12" not in requires_python:
                errors.append(
                    f"Python runtime contract drifted: requires-python={requires_python!r}"
                )

    print(f"PREFLIGHT_SOURCE_PY_FILES={source_count}")
    print(f"PREFLIGHT_TEST_PY_FILES={test_count}")
    print(f"PREFLIGHT_SCRIPT_PY_FILES={script_count}")
    return errors


def main() -> int:
    errors = _failures()
    if errors:
        print("REPOSITORY_PREFLIGHT=FAIL")
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("REPOSITORY_PREFLIGHT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
