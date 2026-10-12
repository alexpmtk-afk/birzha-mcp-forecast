"""Validate, reconcile and export saved prospective scorecards without writes to evidence."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import date
import io
import json
import math
from pathlib import Path
import re
import sys
from typing import Any
from zoneinfo import ZoneInfo

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.g2_prospective_baseline_scorecard import (
    DUCKDB_LEDGER_VERSION, HORIZONS, LEDGER_VERSION, RECORD_VERSIONS, SCORECARD_VERSION,
    ScorecardRefused, _canonical_json, _finite_number, _sha, _time,
    build_scorecard, render_scorecard_text,
)

DECISIONS = {"UP": "рост", "DOWN": "снижение", "NEUTRAL": "нейтральное решение", "ABSTAIN": "явный отказ"}
REASONS = {"PENDING_OUTCOME": "результат ещё не получен", "NEUTRAL_DECISION": "нет направленного решения",
           "EXPLICIT_ABSTENTION": "прогноз явно отказался от направления"}
ROW_FIELDS = {
    "forecast_id", "market", "secid", "horizon_sessions", "decision", "created_at_t0", "captured_at",
    "forecast_sha256", "capture_receipt_sha256", "capture_source_sha256", "reference_price",
    "outcome_status", "comparison_included", "comparison_exclusion_reasons", "actual_return_pct",
    "target_close", "target_session", "target_session_end", "future_source_sha256", "outcome_sha256",
    "candidate_correct", "always_up_correct_same_row",
    "forecast_record_version", "engine_version", "abstention_reasons",
    "explicit_abstention_fields_present", "expected_move_pct", "implied_target_price",
    "absolute_move_error_percentage_points", "flat_price_absolute_error_percentage_points",
    "target_level", "stop_level", "target_close_status", "stop_close_status",
}
COUNT_FIELDS = {
    "forecasts_frozen", "outcomes_completed", "outcomes_pending", "directional_forecasts",
    "explicit_abstentions", "neutral_forecasts", "directional_forecasts_scored", "candidate_correct",
    "always_up_correct_same_rows", "numeric_move_forecasts", "numeric_move_forecasts_scored",
}
RATE_FIELDS = {"candidate_accuracy", "always_up_accuracy_same_rows", "accuracy_difference_percentage_points",
               "directional_coverage_of_frozen_forecasts", "mean_absolute_move_error_percentage_points",
               "flat_price_mean_absolute_error_percentage_points", "numeric_minus_flat_error_percentage_points"}


def _refuse(message: str) -> None:
    raise ScorecardRefused(message)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _refuse(f"Пустое или неверное поле: {label}.")
    return value


def _digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        _refuse(f"Неверная контрольная сумма: {label}.")


def _identity(row: dict[str, Any]) -> tuple[str, str, int]:
    market, secid = _text(row.get("market"), "рынок"), _text(row.get("secid"), "инструмент")
    horizon = row.get("horizon_sessions")
    if type(horizon) is not int or horizon not in HORIZONS:
        _refuse("Срок должен быть равен 5, 10 или 20 торговым сессиям.")
    return market, secid, horizon


def validate_scorecard(report: Any) -> dict[str, Any]:
    """Check internal consistency; external evidence is checked only with match_ledger."""
    if not isinstance(report, dict):
        _refuse("Отчёт должен быть объектом с отдельными записями и итогами.")
    top_fields = {"scorecard_version", "ledger_version", "storage_format", "future_evidence_validation",
                  "read_only", "comparison", "metric", "groups", "observations", "coverage_summary",
                  "limits", "report_sha256"}
    if set(report) != top_fields:
        _refuse("Состав отчёта неизвестен или устарел; постройте его заново из журнала.")
    _digest(report["report_sha256"], "отчёт")
    unsigned = {key: value for key, value in report.items() if key != "report_sha256"}
    try:
        checksum = _sha(_canonical_json(unsigned))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScorecardRefused("Отчёт содержит недопустимые числа или значения.") from exc
    if checksum != report["report_sha256"]:
        _refuse("Контрольная сумма отчёта не совпадает с его содержимым.")
    if (report["scorecard_version"] != SCORECARD_VERSION or
            report["ledger_version"] not in (LEDGER_VERSION, DUCKDB_LEDGER_VERSION) or report["read_only"] is not True):
        _refuse("Неизвестная версия или неподтверждённый режим чтения отчёта.")
    expected_format = "sqlite3 pilot" if report["ledger_version"] == LEDGER_VERSION else "DuckDB staging journal"
    if report["storage_format"] != expected_format:
        _refuse("Формат журнала не соответствует его версии.")
    if (report["comparison"] != "fixed always-UP reference on the same completed outcomes as directional forecasts" or
            report["metric"] != "horizon-end close direction and optional expected-move absolute error against fixed zero-move price on the same rows; a flat return is incorrect for either directional call"):
        _refuse("Правило сравнения или способ оценки изменены.")
    expected_evidence = (
        "SQLite pilot: source-byte hash binding and recorded price/time consistency only; individual future candles are not preserved as structured evidence."
        if report["ledger_version"] == LEDGER_VERSION else
        "DuckDB staging: source-byte hash binding and complete structured future-input window matched to canonical Outcome; raw source bytes are not parsed by this evaluator."
    )
    expected_limits = [
        "This reports descriptive counts, directional accuracy, and optional numeric move error only; it does not select or tune a model.",
        "The prospective pilot ledger is local evidence; hashes do not authenticate an outside source, clock, or calendar.",
        "This is not first-touch outcome scoring, profitability, independent out-of-sample proof, or release acceptance.",
        "Overlapping forecast horizons and repeated origins are dependent; no independent-sample confidence claim is made.",
        "Pending outcomes remain pending and are never filled with zero or inferred from a later forecast.",
    ]
    if report["future_evidence_validation"] != expected_evidence or report["limits"] != expected_limits:
        _refuse("Изменены границы проверки или ограничения оценки.")
    if not isinstance(report["groups"], list) or not isinstance(report["observations"], list):
        _refuse("Итоги и отдельные записи должны быть списками.")

    by_group: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    by_forecast: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    for row in report["observations"]:
        if not isinstance(row, dict) or set(row) != ROW_FIELDS:
            _refuse("Неверный состав отдельной записи прогноза.")
        key = _identity(row)
        fid = _text(row["forecast_id"], "номер прогноза")
        pair = (fid, key[2])
        if pair in seen:
            _refuse("Одна пара прогноз/срок присутствует в отчёте дважды.")
        seen.add(pair)
        direction = row["decision"]
        if row["forecast_record_version"] not in tuple(RECORD_VERSIONS):
            _refuse("Неизвестная версия исходной записи прогноза.")
        if row["engine_version"] is not None:
            _text(row["engine_version"], "версия прогнозной модели")
        if not isinstance(direction, str) or direction not in DECISIONS:
            _refuse("Неизвестное направление прогноза.")
        if type(row["explicit_abstention_fields_present"]) is not bool:
            _refuse("Неверный признак наличия полей явного отказа.")
        if direction == "ABSTAIN" and not row["explicit_abstention_fields_present"]:
            _refuse("Явный отказ невозможен без полей причины в исходной записи.")
        source_reasons = row["abstention_reasons"]
        if (not isinstance(source_reasons, list) or
                not all(isinstance(reason, str) and reason.strip() for reason in source_reasons)):
            _refuse("Исходная причина отказа должна быть непустым текстом.")
        if ((direction == "ABSTAIN") != bool(source_reasons)):
            _refuse("Исходные причины должны быть только у явного отказа от направления.")
        for field in ("forecast_sha256", "capture_receipt_sha256", "capture_source_sha256"):
            _digest(row[field], field)
        t0 = _time(row["created_at_t0"], "время прогноза")
        if _time(row["captured_at"], "время сохранения") < t0:
            _refuse("Запись сохранена раньше времени прогноза.")
        reference = _finite_number(row["reference_price"], "исходная цена")
        if reference <= 0:
            _refuse("Исходная цена должна быть положительной.")
        target_level = row["target_level"]
        stop_level = row["stop_level"]
        if target_level is not None:
            target_level = _finite_number(target_level, "целевой уровень")
        if stop_level is not None:
            stop_level = _finite_number(stop_level, "защитный уровень")
        if direction in ("NEUTRAL", "ABSTAIN") and (target_level is not None or stop_level is not None):
            _refuse("Нейтральное решение или отказ не должны иметь уровни.")
        if direction == "UP" and ((target_level is not None and target_level <= row["reference_price"]) or (stop_level is not None and stop_level >= row["reference_price"])):
            _refuse("Уровни роста расположены по неправильную сторону исходной цены.")
        if direction == "DOWN" and ((target_level is not None and target_level >= row["reference_price"]) or (stop_level is not None and stop_level <= row["reference_price"])):
            _refuse("Уровни снижения расположены по неправильную сторону исходной цены.")
        expected_move = row["expected_move_pct"]
        if expected_move is None:
            if (row["implied_target_price"] is not None or row["absolute_move_error_percentage_points"] is not None
                    or row["flat_price_absolute_error_percentage_points"] is not None):
                _refuse("Числовая оценка указана без исходного числового прогноза.")
        else:
            expected_move = _finite_number(expected_move, "ожидаемое движение")
            raw_price = reference * (1 + expected_move / 100)
            if direction == "ABSTAIN" or not math.isfinite(raw_price) or raw_price <= 0:
                _refuse("Числовой прогноз противоречит решению или исходной цене.")
            implied = round(raw_price, 8)
            if implied <= 0 or row["implied_target_price"] != implied:
                _refuse("Расчётная цена не соответствует числовому прогнозу.")
        status = row["outcome_status"]
        if status not in ("PENDING", "OBSERVED"):
            _refuse("Неизвестное состояние исхода.")
        reasons = [] if status == "OBSERVED" else ["PENDING_OUTCOME"]
        if direction == "NEUTRAL":
            reasons.append("NEUTRAL_DECISION")
        elif direction == "ABSTAIN":
            reasons.append("EXPLICIT_ABSTENTION")
        if row["comparison_exclusion_reasons"] != reasons:
            _refuse("Причины исключения не соответствуют направлению и исходу.")
        included = status == "OBSERVED" and direction in ("UP", "DOWN")
        if type(row["comparison_included"]) is not bool or row["comparison_included"] is not included:
            _refuse("Неверно указано участие записи в сравнении.")
        if status == "PENDING":
            if row["target_close_status"] is not None or row["stop_close_status"] is not None:
                _refuse("Ожидающий исход не должен иметь итоговый статус уровня.")
            if any(row[field] is not None for field in (
                "actual_return_pct", "target_close", "target_session", "target_session_end",
                "future_source_sha256", "outcome_sha256", "candidate_correct", "always_up_correct_same_row",
                "absolute_move_error_percentage_points", "flat_price_absolute_error_percentage_points",
            )):
                _refuse("Ожидающий исход содержит выдуманную цену или оценку.")
        else:
            if row["target_level"] is None and row["target_close_status"] != "NOT_SET":
                _refuse("Статус цели не соответствует отсутствию целевого уровня.")
            if row["stop_level"] is None and row["stop_close_status"] != "NOT_SET":
                _refuse("Статус защиты не соответствует отсутствию защитного уровня.")
            _digest(row["future_source_sha256"], "источник исхода")
            _digest(row["outcome_sha256"], "запись исхода")
            target = _finite_number(row["target_close"], "цена исхода")
            actual = _finite_number(row["actual_return_pct"], "изменение цены")
            if target <= 0 or actual != round((target / reference - 1.0) * 100.0, 8):
                _refuse("Изменение цены не соответствует исходной и конечной цене.")
            if row["target_level"] is not None:
                expected_target_status = "REACHED_AT_CLOSE" if (target > row["target_level"] if direction == "UP" else target < row["target_level"]) else "NOT_REACHED_AT_CLOSE"
                if row["target_close_status"] != expected_target_status:
                    _refuse("Статус целевого уровня не соответствует конечной цене.")
            if row["stop_level"] is not None:
                expected_stop_status = "REACHED_AT_CLOSE" if (target < row["stop_level"] if direction == "UP" else target > row["stop_level"]) else "NOT_REACHED_AT_CLOSE"
                if row["stop_close_status"] != expected_stop_status:
                    _refuse("Статус защитного уровня не соответствует конечной цене.")
            expected_error = None if expected_move is None else round(abs(expected_move - actual), 8)
            if row["absolute_move_error_percentage_points"] != expected_error:
                _refuse("Ошибка числового прогноза не соответствует записанному исходу.")
            flat_error = None if expected_move is None else round(abs(actual), 8)
            if row["flat_price_absolute_error_percentage_points"] != flat_error:
                _refuse("Ошибка ориентира постоянной цены не соответствует исходу.")
            end = _time(row["target_session_end"], "время закрытия исхода")
            try:
                day = date.fromisoformat(row["target_session"])
            except (ValueError, TypeError) as exc:
                raise ScorecardRefused("Неверная дата сессии исхода.") from exc
            if (day.isoformat() != row["target_session"] or day <= t0.date() or end <= t0 or
                    (end.astimezone(ZoneInfo("Europe/Moscow")).date() - day).days not in (0, 1)):
                _refuse("Дата и время исхода не согласованы с прогнозом.")
            candidate = (actual > 0 if direction == "UP" else actual < 0) if included else None
            baseline = actual > 0 if included else None
            if row["candidate_correct"] is not candidate or row["always_up_correct_same_row"] is not baseline:
                _refuse("Оценка прогноза или правила «всегда рост» неверна.")
        by_group[key].append(row)
        by_forecast[fid].append(row)

    origin_fields = ("market", "secid", "created_at_t0", "captured_at", "reference_price",
                     "forecast_record_version", "engine_version",
                     "forecast_sha256", "capture_receipt_sha256", "capture_source_sha256")
    for rows in by_forecast.values():
        if {row["horizon_sessions"] for row in rows} != set(HORIZONS):
            _refuse("У прогноза отсутствует один из сроков 5/10/20.")
        if any(tuple(row[field] for field in origin_fields) != tuple(rows[0][field] for field in origin_fields) for row in rows[1:]):
            _refuse("Сроки одного прогноза ссылаются на разные исходные записи.")

    group_keys = set()
    for group in report["groups"]:
        if not isinstance(group, dict) or set(group) != {"market", "secid", "horizon_sessions"} | COUNT_FIELDS | RATE_FIELDS:
            _refuse("Неверный состав итоговой строки.")
        key = _identity(group)
        if key in group_keys or key not in by_group:
            _refuse("Итоговая группа повторяется или не имеет отдельных записей.")
        group_keys.add(key)
        for field in COUNT_FIELDS:
            if type(group[field]) is not int or group[field] < 0:
                _refuse("Количество записей должно быть целым неотрицательным числом.")
        for field in RATE_FIELDS:
            if group[field] is not None:
                _finite_number(group[field], "доля или разница точности")
        rows = by_group[key]
        scored = [row for row in rows if row["comparison_included"]]
        candidate = sum(row["candidate_correct"] for row in scored)
        baseline = sum(row["always_up_correct_same_row"] for row in scored)
        n = len(scored)
        numeric_errors = [row["absolute_move_error_percentage_points"] for row in rows
                          if row["absolute_move_error_percentage_points"] is not None]
        flat_errors = [row["flat_price_absolute_error_percentage_points"] for row in rows
                       if row["flat_price_absolute_error_percentage_points"] is not None]
        accuracy, up_accuracy = (candidate / n, baseline / n) if n else (None, None)
        expected = {
            "market": key[0], "secid": key[1], "horizon_sessions": key[2],
            "forecasts_frozen": len(rows),
            "outcomes_completed": sum(row["outcome_status"] == "OBSERVED" for row in rows),
            "outcomes_pending": sum(row["outcome_status"] == "PENDING" for row in rows),
            "directional_forecasts": sum(row["decision"] in ("UP", "DOWN") for row in rows),
            "explicit_abstentions": sum(row["decision"] == "ABSTAIN" for row in rows),
            "neutral_forecasts": sum(row["decision"] == "NEUTRAL" for row in rows),
            "directional_forecasts_scored": n, "candidate_correct": candidate,
            "numeric_move_forecasts": sum(row["expected_move_pct"] is not None for row in rows),
            "numeric_move_forecasts_scored": len(numeric_errors),
            "mean_absolute_move_error_percentage_points": sum(numeric_errors) / len(numeric_errors) if numeric_errors else None,
            "flat_price_mean_absolute_error_percentage_points": sum(flat_errors) / len(flat_errors) if flat_errors else None,
            "numeric_minus_flat_error_percentage_points": (sum(numeric_errors) - sum(flat_errors)) / len(numeric_errors) if numeric_errors else None,
            "always_up_correct_same_rows": baseline, "candidate_accuracy": accuracy,
            "always_up_accuracy_same_rows": up_accuracy,
            "accuracy_difference_percentage_points": (accuracy - up_accuracy) * 100.0 if n else None,
            "directional_coverage_of_frozen_forecasts": sum(row["decision"] in ("UP", "DOWN") for row in rows) / len(rows),
        }
        if group != expected:
            _refuse("Итоговые числа не совпадают с отдельными записями прогноза.")
    if group_keys != set(by_group):
        _refuse("Для части отдельных записей отсутствуют итоги.")
    # Recompute the project-wide coverage totals from the separate forecast rows.
    rows = report["observations"]
    def summarize_coverage(part: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {name: sum(row["decision"] == name for row in part)
                  for name in ("UP", "DOWN", "NEUTRAL", "ABSTAIN")}
        n_directional = counts["UP"] + counts["DOWN"]
        counts_by_reason: dict[str, dict[str, Any]] = {}
        for row in part:
            for reason in row["abstention_reasons"]:
                entry = counts_by_reason.setdefault(reason, {"rows": 0, "forecasts": set()})
                entry["rows"] += 1
                entry["forecasts"].add(row["forecast_id"])
        return {
            "forecast_horizon_rows": len(part), "directional_rows": n_directional,
            "neutral_rows": counts["NEUTRAL"], "explicit_abstention_rows": counts["ABSTAIN"],
            "rows_with_explicit_abstention_fields": sum(row["explicit_abstention_fields_present"] for row in part),
            "pending_outcome_rows": sum(row["outcome_status"] == "PENDING" for row in part),
            "completed_outcome_rows": sum(row["outcome_status"] == "OBSERVED" for row in part),
            "numeric_move_rows": sum(row["expected_move_pct"] is not None for row in part),
            "numeric_move_scored_rows": sum(row["absolute_move_error_percentage_points"] is not None for row in part),
            "directional_coverage": n_directional / len(part) if part else None,
            "abstention_reasons": [
                {"reason": reason, "forecast_horizon_rows": item["rows"],
                 "unique_forecasts": len(item["forecasts"])}
                for reason, item in sorted(counts_by_reason.items())
            ],
        }

    market_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    horizon_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        market_rows[(row["market"], row["secid"])].append(row)
        horizon_rows[row["horizon_sessions"]].append(row)
    coverage = {
        **summarize_coverage(rows),
        "by_market": [
            {"market": market, "secid": secid, **summarize_coverage(part)}
            for (market, secid), part in sorted(market_rows.items())
        ],
        "by_horizon": [
            {"horizon_sessions": horizon, **summarize_coverage(part)}
            for horizon, part in sorted(horizon_rows.items())
        ],
    }
    if report["coverage_summary"] != coverage:
        _refuse("Общая сводка охвата или исходных причин отказа не совпадает с отдельными записями.")
    return report


def load_scorecard(path: Path) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                _refuse("В файле отчёта повторяется название поля.")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        _refuse("В файле отчёта есть недопустимое числовое значение.")

    try:
        raw = path.read_text(encoding="utf-8-sig")
        report = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ScorecardRefused("Не удалось прочитать файл отчёта; исходный файл не изменён.") from exc
    return validate_scorecard(report)


def match_ledger(report: dict[str, Any], ledger: Path) -> None:
    validate_scorecard(report)
    current = build_scorecard(ledger)
    if report != current:
        _refuse("Отчёт не совпадает с указанным журналом: состав прогнозов или исходов изменился.")


def render_scorecard_csv(report: dict[str, Any]) -> str:
    validate_scorecard(report)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["Номер прогноза", "Рынок", "Точный инструмент", "Срок в торговых сессиях", "Направление",
                     "Время прогноза", "Исходная цена", "Состояние исхода", "Сессия исхода", "Цена закрытия",
                     "Изменение цены в процентах", "Участвует в сравнении", "Причины исключения",
                     "Исходные причины явного отказа", "Ожидаемое движение в процентах",
                     "Расчётная цена в конце срока", "Абсолютная ошибка в процентных пунктах",
                     "Ошибка постоянной цены в процентных пунктах",
                     "Результат прогноза", "Результат правила всегда рост", "Контрольная сумма прогноза",
                     "Контрольная сумма исхода", "Контрольная сумма отчёта"])

    def cell(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    def result(value: bool | None) -> str:
        return "" if value is None else "верно" if value else "ошибка"

    for row in report["observations"]:
        values = [row["forecast_id"], row["market"], row["secid"], row["horizon_sessions"], DECISIONS[row["decision"]],
                  row["created_at_t0"], row["reference_price"], "ожидается" if row["outcome_status"] == "PENDING" else "получен",
                  row["target_session"], row["target_close"], row["actual_return_pct"],
                  "да" if row["comparison_included"] else "нет",
                  "; ".join(REASONS[reason] for reason in row["comparison_exclusion_reasons"]),
                  "; ".join(row["abstention_reasons"]), row["expected_move_pct"],
                  row["implied_target_price"], row["absolute_move_error_percentage_points"],
                  row["flat_price_absolute_error_percentage_points"],
                  result(row["candidate_correct"]), result(row["always_up_correct_same_row"]),
                  row["forecast_sha256"], row["outcome_sha256"], report["report_sha256"]]
        writer.writerow([cell(value) for value in values])
    return buffer.getvalue()


def _protect_output(output: Path, sources: list[Path]) -> None:
    protected = [path for source in sources for path in
                 (source, *(Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal", ".wal")))]
    try:
        if any(output.resolve() == path.resolve() or
               (output.exists() and path.exists() and output.samefile(path)) for path in protected):
            _refuse("Выходной файл совпадает с исходным отчётом, журналом или его служебным файлом.")
    except OSError as exc:
        raise ScorecardRefused("Не удалось проверить путь выходного файла.") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить сохранённый отчёт или журнал и выгрузить согласованные результаты; только чтение.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ledger", type=Path, help="изолированный журнал прогнозов")
    source.add_argument("--report", type=Path, help="сохранённый машинный отчёт")
    parser.add_argument("--match-ledger", type=Path, help="дополнительно сверить сохранённый отчёт с исходным журналом")
    parser.add_argument("--format", choices=("json", "text", "csv"), default="text")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="показать только заключение проверки")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.match_ledger is not None and args.report is None:
        parser.error("--match-ledger применяется вместе с --report")
    if args.check_only and args.output is not None:
        parser.error("--check-only не записывает выходной файл")
    sources = [path for path in (args.ledger, args.report, args.match_ledger) if path is not None]
    try:
        if args.output is not None:
            _protect_output(args.output, sources)
        report = load_scorecard(args.report) if args.report is not None else validate_scorecard(build_scorecard(args.ledger))
        if args.match_ledger is not None:
            match_ledger(report, args.match_ledger)
        if args.check_only:
            print("Отчёт согласован: контрольная сумма, отдельные записи и итоговые числа совпадают.")
            print("Связь с журналом проверена." if args.ledger is not None or args.match_ledger is not None else
                  "Проверено содержимое отчёта; для связи с исходными данными укажите журнал.")
            return 0
        rendered = (render_scorecard_csv(report) if args.format == "csv" else
                    render_scorecard_text(report, include_details=args.details) if args.format == "text" else
                    json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.write_text(rendered, encoding="utf-8-sig" if args.format == "csv" else "utf-8", newline="")
            print(f"Проверенный отчёт сохранён: {args.output}")
    except ScorecardRefused as exc:
        print(f"Проверка остановлена: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError) as exc:
        print("Не удалось прочитать или записать файл; оценка не подтверждена.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
