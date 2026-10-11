"""Build one verified assessment package from compatible, disjoint local journals."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import shutil
import sys
import uuid
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.g2_prospective_baseline_scorecard import (
    ScorecardRefused, _canonical_json, _sha, build_scorecard, render_scorecard_text,
)
from scripts.g2_prospective_scorecard_review import load_scorecard, render_scorecard_csv, validate_scorecard

MANIFEST_VERSION = "G2_SCORECARD_PACKAGE_V1"


def combine_scorecards(reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not reports:
        raise ScorecardRefused("Не указаны журналы для общей оценки.")
    metadata = ("scorecard_version", "ledger_version", "storage_format", "future_evidence_validation",
                "read_only", "comparison", "metric", "limits")
    seen: set[str] = set()
    candidate = None
    observations = []
    for report in reports:
        validate_scorecard(report)
        if any(report[field] != reports[0][field] for field in metadata):
            raise ScorecardRefused("Версии или правила оценки журналов различаются; общий подсчёт запрещён.")
        forecasts = {row["forecast_id"] for row in report["observations"]}
        if seen.intersection(forecasts):
            raise ScorecardRefused("Прогноз встречается в нескольких журналах; повторный учёт запрещён.")
        seen.update(forecasts)
        for row in report["observations"]:
            if row["engine_version"] is None:
                raise ScorecardRefused("Версия прогнозной модели неизвестна; общий подсчёт запрещён.")
            identity = (row["forecast_record_version"], row["engine_version"])
            if candidate is None:
                candidate = identity
            elif candidate != identity:
                raise ScorecardRefused("Прогнозы относятся к разным версиям модели или записи; общий подсчёт запрещён.")
            observations.append(dict(row))
    observations.sort(key=lambda row: (row["market"], row["secid"], row["horizon_sessions"], row["forecast_id"]))
    buckets = defaultdict(list)
    for row in observations:
        buckets[(row["market"], row["secid"], row["horizon_sessions"])].append(row)
    groups = []
    for (market, secid, horizon), rows in sorted(buckets.items()):
        scored = [row for row in rows if row["comparison_included"]]
        n = len(scored)
        correct = sum(row["candidate_correct"] for row in scored)
        up_correct = sum(row["always_up_correct_same_row"] for row in scored)
        accuracy, up_accuracy = (correct / n, up_correct / n) if n else (None, None)
        directional = sum(row["decision"] in ("UP", "DOWN") for row in rows)
        groups.append({
            "market": market, "secid": secid, "horizon_sessions": horizon,
            "forecasts_frozen": len(rows),
            "outcomes_completed": sum(row["outcome_status"] == "OBSERVED" for row in rows),
            "outcomes_pending": sum(row["outcome_status"] == "PENDING" for row in rows),
            "directional_forecasts": directional,
            "explicit_abstentions": sum(row["decision"] == "ABSTAIN" for row in rows),
            "neutral_forecasts": sum(row["decision"] == "NEUTRAL" for row in rows),
            "directional_forecasts_scored": n, "candidate_correct": correct,
            "always_up_correct_same_rows": up_correct, "candidate_accuracy": accuracy,
            "always_up_accuracy_same_rows": up_accuracy,
            "accuracy_difference_percentage_points": (accuracy - up_accuracy) * 100. if n else None,
            "directional_coverage_of_frozen_forecasts": directional / len(rows),
        })
    result = {field: reports[0][field] for field in metadata}
    result["groups"], result["observations"] = groups, observations
    result["report_sha256"] = _sha(_canonical_json(result))
    return validate_scorecard(result)


def _encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _members(reports: list[dict[str, Any]], combined: dict[str, Any]) -> dict[str, bytes]:
    result = {
        "combined.json": _encode(combined),
        "combined.csv": b"\xef\xbb\xbf" + render_scorecard_csv(combined).encode("utf-8"),
        "combined.txt": render_scorecard_text(combined, include_details=True).encode("utf-8"),
    }
    for index, report in enumerate(reports, 1):
        stem = f"journal_{index:03d}"
        result[stem + ".json"] = _encode(report)
        result[stem + ".csv"] = b"\xef\xbb\xbf" + render_scorecard_csv(report).encode("utf-8")
        result[stem + ".txt"] = render_scorecard_text(report, include_details=True).encode("utf-8")
    return result


def verify_package(directory: Path) -> dict[str, Any]:
    """Check every member, regenerated exports and exact combined counts."""
    try:
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink():
            raise ScorecardRefused("Описание комплекта не должно быть ссылкой на другой файл.")
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result = {}
            for key, value in items:
                if key in result:
                    raise ScorecardRefused("В описании комплекта повторяется название поля.")
                result[key] = value
            return result
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
        if not isinstance(manifest, dict) or set(manifest) != {"manifest_version", "journals", "files", "manifest_sha256"}:
            raise ScorecardRefused("Неверный состав описания комплекта.")
        if manifest["manifest_version"] != MANIFEST_VERSION:
            raise ScorecardRefused("Неизвестная версия комплекта.")
        unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        if manifest["manifest_sha256"] != _sha(_canonical_json(unsigned)):
            raise ScorecardRefused("Контрольная сумма описания комплекта не совпадает.")
        journals, files = manifest["journals"], manifest["files"]
        if not isinstance(journals, list) or not journals or not isinstance(files, dict):
            raise ScorecardRefused("В комплекте отсутствуют журналы или список файлов.")
        expected_names = {"combined.json", "combined.csv", "combined.txt"}
        reports = []
        for index, entry in enumerate(journals, 1):
            stem = f"journal_{index:03d}"
            if (not isinstance(entry, dict) or set(entry) != {"report_file", "report_sha256", "source_path"} or
                    entry["report_file"] != stem + ".json" or not isinstance(entry["source_path"], str) or not entry["source_path"]):
                raise ScorecardRefused("Неверная связь журнала с файлом отчёта.")
            expected_names.update(stem + suffix for suffix in (".json", ".csv", ".txt"))
            report = load_scorecard(directory / entry["report_file"])
            if report["report_sha256"] != entry["report_sha256"]:
                raise ScorecardRefused("Контрольная сумма отдельного отчёта не соответствует описанию.")
            reports.append(report)
        if set(files) != expected_names:
            raise ScorecardRefused("Список файлов комплекта неполон или содержит посторонние имена.")
        if {path.name for path in directory.iterdir()} != expected_names | {"manifest.json"}:
            raise ScorecardRefused("Состав папки не совпадает с описанием комплекта.")
        combined = combine_scorecards(reports)
        expected = _members(reports, combined)
        for name, content in expected.items():
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content or files[name] != _sha(content):
                raise ScorecardRefused("Содержимое файла комплекта изменено или не соответствует проверенным отчётам.")
        return manifest
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ScorecardRefused):
            raise
        raise ScorecardRefused("Не удалось проверить комплект; его содержимое не изменялось.") from exc


def build_package(paths: list[Path], output: Path) -> dict[str, Any]:
    if not paths:
        raise ScorecardRefused("Не указаны журналы для общей оценки.")
    sources = [path.resolve(strict=True) for path in paths]
    if len(set(sources)) != len(sources) or any(
        first.samefile(second) for i, first in enumerate(sources) for second in sources[i + 1:]
    ):
        raise ScorecardRefused("Один журнал указан несколько раз или через разные ссылки.")
    parent = output.parent.resolve(strict=True)
    target = parent / output.name
    if target.exists() or target.is_symlink():
        raise ScorecardRefused("Папка результата уже существует; укажите новую папку.")
    if any(source.is_relative_to(target) for source in sources):
        raise ScorecardRefused("Журнал расположен внутри папки результата.")
    pairs = []
    for source in sources:
        pairs.append((validate_scorecard(build_scorecard(source)), source))
    pairs.sort(key=lambda pair: (pair[0]["report_sha256"], str(pair[1])))
    reports = [report for report, source in pairs]
    combined = combine_scorecards(reports)
    contents = _members(reports, combined)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "journals": [{"report_file": f"journal_{index:03d}.json", "report_sha256": report["report_sha256"],
                      "source_path": str(source)} for index, (report, source) in enumerate(pairs, 1)],
        "files": {name: _sha(content) for name, content in contents.items()},
    }
    manifest["manifest_sha256"] = _sha(_canonical_json(manifest))
    stage = parent / (".g2-scorecard-package-" + uuid.uuid4().hex)
    stage.mkdir()
    try:
        for name, content in contents.items():
            (stage / name).write_bytes(content)
        (stage / "manifest.json").write_bytes(_encode(manifest))
        verify_package(stage)
        # A later check catches journals changed while other inputs were read/exported.
        for report, source in pairs:
            if validate_scorecard(build_scorecard(source)) != report:
                raise ScorecardRefused("Журнал изменился во время общего запуска; комплект не опубликован.")
        if target.exists() or target.is_symlink():
            raise ScorecardRefused("Папка результата появилась во время запуска; её содержимое сохранено.")
        stage.rename(target)
        return manifest
    finally:
        # Remove only our unpublished staging directory under the resolved parent.
        if stage.exists() and not stage.is_symlink() and stage.resolve().parent == parent and stage.name.startswith(".g2-scorecard-package-"):
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Оценить несколько журналов одной командой; создать новый проверенный комплект.")
    parser.add_argument("--ledger", action="append", type=Path, help="повторите для каждого журнала")
    parser.add_argument("--output-dir", type=Path, help="новая папка под существующей родительской папкой")
    parser.add_argument("--verify-package", type=Path, help="проверить ранее созданный комплект без записи")
    args = parser.parse_args(argv)
    if args.verify_package is not None and (args.ledger is not None or args.output_dir is not None):
        parser.error("проверка готового комплекта применяется отдельно")
    if args.verify_package is None and (not args.ledger or args.output_dir is None):
        parser.error("укажите журналы и новую папку результата")
    try:
        if args.verify_package is not None:
            verify_package(args.verify_package)
            print("Комплект согласован: файлы, отдельные отчёты и общие итоги совпадают.")
            print("Проверено сохранённое содержимое; исходные журналы повторно не открывались.")
        else:
            manifest = build_package(args.ledger, args.output_dir)
            print(f"Готов проверенный комплект: {args.output_dir}. Журналов: {len(manifest['journals'])}.")
            print("Каждый журнал прочитан повторно; повторные прогнозы и несовместимые версии исключены.")
    except (ScorecardRefused, OSError, UnicodeError) as exc:
        print(f"Общий запуск остановлен: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
