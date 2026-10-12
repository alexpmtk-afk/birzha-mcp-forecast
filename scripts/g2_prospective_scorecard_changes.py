"""Compare verified assessment packages as an append-only evolution of evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.g2_prospective_baseline_scorecard import ScorecardRefused, _canonical_json, _sha
from scripts.g2_prospective_scorecard_batch import verify_package
from scripts.g2_prospective_scorecard_review import load_scorecard, validate_scorecard

CHANGE_VERSION = "G2_SCORECARD_CHANGES_V1"
MUTABLE_FIELDS = {
    "outcome_status", "comparison_included", "comparison_exclusion_reasons", "actual_return_pct",
    "target_close", "target_session", "target_session_end", "future_source_sha256", "outcome_sha256",
    "candidate_correct", "always_up_correct_same_row",
    "target_close_statuses", "stop_close_status",
    "absolute_move_error_percentage_points", "flat_price_absolute_error_percentage_points",
}


def compare_scorecards(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    validate_scorecard(before)
    validate_scorecard(after)
    for field in ("scorecard_version", "ledger_version", "storage_format", "future_evidence_validation",
                  "read_only", "comparison", "metric", "limits"):
        if before[field] != after[field]:
            raise ScorecardRefused("Версии или правила оценки различаются; сопоставление запрещено.")
    old = {(row["forecast_id"], row["horizon_sessions"]): row for row in before["observations"]}
    new = {(row["forecast_id"], row["horizon_sessions"]): row for row in after["observations"]}
    if set(old) - set(new):
        raise ScorecardRefused("Прежние прогнозы исчезли из нового комплекта; последовательность не подтверждена.")
    newly_observed = []
    unchanged = 0
    for pair, old_row in old.items():
        new_row = new[pair]
        if {key: value for key, value in old_row.items() if key not in MUTABLE_FIELDS} != {
            key: value for key, value in new_row.items() if key not in MUTABLE_FIELDS
        }:
            raise ScorecardRefused("Прежний прогноз или его исходная запись изменены.")
        if old_row["outcome_status"] == "OBSERVED":
            if new_row != old_row:
                raise ScorecardRefused("Прежний записанный исход изменён или возвращён в ожидание.")
        elif new_row["outcome_status"] == "OBSERVED":
            newly_observed.append(dict(new_row))
        elif new_row != old_row:
            raise ScorecardRefused("Ожидающая запись изменена без получения исхода.")
        else:
            unchanged += 1
    old_ids = {row["forecast_id"] for row in old.values()}
    new_ids = {row["forecast_id"] for row in new.values()} - old_ids
    added = [dict(row) for row in after["observations"] if row["forecast_id"] in new_ids]
    identity = lambda row: (row["market"], row["secid"], row["horizon_sessions"], row["forecast_id"])
    newly_observed.sort(key=identity)
    added.sort(key=identity)
    old_groups = {(row["market"], row["secid"], row["horizon_sessions"]): row for row in before["groups"]}
    group_changes = []
    count_fields = ("forecasts_frozen", "outcomes_completed", "outcomes_pending", "directional_forecasts_scored",
                    "candidate_correct", "always_up_correct_same_rows")
    for row in after["groups"]:
        key = (row["market"], row["secid"], row["horizon_sessions"])
        previous = old_groups.get(key)
        deltas = {field: row[field] - (previous[field] if previous is not None else 0) for field in count_fields}
        accuracy_change = None
        if previous is not None and previous["candidate_accuracy"] is not None and row["candidate_accuracy"] is not None:
            accuracy_change = (row["candidate_accuracy"] - previous["candidate_accuracy"]) * 100.
        group_changes.append({"market": key[0], "secid": key[1], "horizon_sessions": key[2],
                              "before": previous, "after": dict(row), "count_changes": deltas,
                              "candidate_accuracy_change_percentage_points": accuracy_change})
    group_changes.sort(key=lambda row: (row["market"], row["secid"], row["horizon_sessions"]))
    result = {
        "change_version": CHANGE_VERSION, "before_report_sha256": before["report_sha256"],
        "after_report_sha256": after["report_sha256"], "read_only": True,
        "new_forecast_ids": sorted(new_ids), "new_forecast_horizon_rows": added,
        "newly_observed_existing_rows": newly_observed,
        "existing_pending_rows_unchanged": unchanged,
        "existing_observed_rows_unchanged": sum(row["outcome_status"] == "OBSERVED" for row in old.values()),
        "pending_rows_now": sum(row["outcome_status"] == "PENDING" for row in new.values()),
        "group_changes": group_changes,
        "limits": [
            "Append-only consistency between two saved reports, not authentication of a source or global chronology.",
            "New forecast origins and overlapping horizons change the observation set; changes in aggregate accuracy do not prove model improvement.",
            "Saved packages are verified; current source journals are not reopened by this comparison.",
        ],
    }
    result["change_sha256"] = _sha(_canonical_json(result))
    return result


def compare_packages(before: Path, after: Path) -> dict[str, Any]:
    old_manifest = verify_package(before)
    new_manifest = verify_package(after)
    old = load_scorecard(before / "combined.json")
    new = load_scorecard(after / "combined.json")
    result = compare_scorecards(old, new)
    if verify_package(before) != old_manifest or verify_package(after) != new_manifest:
        raise ScorecardRefused("Комплект изменился во время сопоставления; результат не подтверждён.")
    result["before_package_sha256"] = old_manifest["manifest_sha256"]
    result["after_package_sha256"] = new_manifest["manifest_sha256"]
    result.pop("change_sha256")
    result["change_sha256"] = _sha(_canonical_json(result))
    return result


def render_changes_text(report: dict[str, Any]) -> str:
    lines = ["Изменения оценки сохранённых прогнозов", "",
             "Прежние прогнозы и уже полученные исходы сохранены без изменений.",
             f"Новых прогнозов: {len(report['new_forecast_ids'])}. Их строк по срокам: {len(report['new_forecast_horizon_rows'])}.",
             f"Получено исходов для прежних прогнозов: {len(report['newly_observed_existing_rows'])}.",
             f"Прежних ожиданий без изменений: {report['existing_pending_rows_unchanged']}.",
             f"Прежних полученных исходов без изменений: {report['existing_observed_rows_unchanged']}.",
             f"Всего ожидающих исходов сейчас: {report['pending_rows_now']}."]
    for row in report["group_changes"]:
        previous, current = row["before"], row["after"]
        label = lambda value: " ".join(str(value).splitlines())
        lines.extend(["", f"Рынок: {label(row['market'])}. Инструмент: {label(row['secid'])}. Срок: {row['horizon_sessions']} торговых сессий.",
                      f"Завершённых направленных оценок было: {previous['directional_forecasts_scored'] if previous else 0}. Сейчас: {current['directional_forecasts_scored']}.",
                      f"Полученных исходов добавилось: {row['count_changes']['outcomes_completed']}. Ожидающих сейчас: {current['outcomes_pending']}."])
        if current["candidate_accuracy"] is None:
            lines.append("Точность пока не рассчитана: завершённых направленных оценок нет.")
        else:
            lines.append(f"Точность прогноза сейчас: {current['candidate_accuracy'] * 100:.2f}%. Правило «всегда рост»: {current['always_up_accuracy_same_rows'] * 100:.2f}%.")
        change = row["candidate_accuracy_change_percentage_points"]
        lines.append("Изменение точности не рассчитано: для сравнения нужны оценки в обоих комплектах." if change is None else
                     f"Изменение точности: {change:+.2f} процентных пунктов.")
    if report["newly_observed_existing_rows"]:
        lines.extend(["", "Новые исходы прежних прогнозов:"])
        for row in report["newly_observed_existing_rows"]:
            lines.append(f"Запись: {' '.join(row['forecast_id'].splitlines())}. Срок: {row['horizon_sessions']}. Сессия: {row['target_session']}. Цена: {row['target_close']}. Изменение: {row['actual_return_pct']}%.")
    lines.extend(["", "Изменение общей точности может быть связано с изменением состава наблюдений; улучшение модели этим не доказано.",
                  "Проверены сохранённые комплекты. Исходные журналы заново не открывались, подлинность внешнего источника не подтверждается.",
                  f"Контрольная сумма сравнения: {report['change_sha256']}."])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сравнить два сохранённых комплекта оценки без изменения прогнозов и исходов.")
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.output is not None:
            output = args.output.resolve()
            if args.output.exists() or args.output.is_symlink() or any(output.is_relative_to(path.resolve()) for path in (args.before, args.after)):
                raise ScorecardRefused("Выходной файл уже существует или расположен внутри исходного комплекта.")
        report = compare_packages(args.before, args.after)
        rendered = render_changes_text(report) if args.format == "text" else json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(rendered)
            print(f"Сравнение сохранено: {args.output}")
    except (ScorecardRefused, OSError, UnicodeError) as exc:
        print(f"Сравнение остановлено: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
