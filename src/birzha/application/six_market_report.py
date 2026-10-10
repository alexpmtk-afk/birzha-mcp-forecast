"""One read-only inventory of six markets from original bound inputs."""
import hashlib
from birzha.application.combined_market_report import build_combined_market_report
from birzha.application.level_reaction import canonical_bytes

MARKETS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
SIX_MARKET_REPORT_VERSION = "SIX_MARKET_FACTS_V1"
EVIDENCE_MODES = ("OFFLINE_RESEARCH_REPEAT", "FROZEN_RECORD_VIEW")


def build_six_market_report(inputs, *, evidence_mode):
    """Input values are original snapshot/forecast/optional observations, not reports.

    Missing markets stay absent; malformed supplied evidence fails the report.
    Mode describes caller intent, not authenticated forecast issuance.
    """
    if evidence_mode not in EVIDENCE_MODES:
        raise ValueError("explicit supported evidence mode required")
    if not isinstance(inputs, dict) or any(market not in MARKETS for market in inputs):
        raise ValueError("only canonical six-market input keys allowed")
    rows = []
    for market in MARKETS:
        if market not in inputs:
            rows.append({"market": market, "input_status": "NOT_SUPPLIED", "secid": None,
                "t0": None, "timeframes": None, "decision_status": None, "direction": None,
                "abstention_reasons": None, "reference": None, "level_status_counts": None,
                "later_observation": None, "detail_report": None})
            continue
        detail = build_combined_market_report(**inputs[market])
        if detail["symbol"] != market:
            raise ValueError("market inventory key conflicts with snapshot instrument")
        movement = detail["movement_at_t0"]
        decision = detail["decision_at_t0"]
        levels = detail["level_report"]
        timeframes = []
        for tf in ("D1", "H1", "M15"):
            metrics = [next(m for m in w["metrics"] if m["timeframe"] == tf) for w in movement["windows"]]
            timeframes.append({"timeframe": tf, "actual_candles": metrics[0]["actual_candles"],
                "source_end": metrics[0]["source_end"], "windows": [{key: m[key] for key in
                    ("steps", "required_completed_candles", "status", "reason", "direction")} for m in metrics]})
        statuses = {}
        for row in levels["rows"]:
            status = row["origin"]["status"]
            statuses[status] = statuses.get(status, 0) + 1
        later = levels["after_t0"]
        observation = {"status": "NOT_SUPPLIED", "supplied_bar_count": None, "timeframe": None,
            "observation_at": None, "schedule_continuity": None, "available_holding_levels": None}
        if later is not None:
            holding = later["holding"]
            observation = {"status": "SUPPLIED_BARS_ONLY", "supplied_bar_count": later["reaction"]["supplied_bar_count"],
                "timeframe": later["timeframe"], "observation_at": later["observation_at"],
                "schedule_continuity": holding["continuity"],
                "available_holding_levels": sum(row["longest_close_runs"] is not None for row in holding["levels"])}
        rows.append({"market": market, "input_status": "SUPPLIED_AND_CONTENT_BOUND", "secid": detail["secid"],
            "t0": detail["t0"], "timeframes": timeframes, "decision_status": decision["decision_status"],
            "direction": decision["direction"], "abstention_reasons": decision["abstention_reasons"],
            "reference": levels["at_t0"]["reference"], "level_status_counts": statuses,
            "later_observation": observation, "detail_report": detail})
    complete = all(row["input_status"] == "SUPPLIED_AND_CONTENT_BOUND" for row in rows)
    timestamps = {row["t0"] for row in rows if row["t0"] is not None}
    body = {"version": SIX_MARKET_REPORT_VERSION, "evidence_mode": evidence_mode, "rows": rows,
        "supplied_market_count": len(inputs), "missing_markets": [m for m in MARKETS if m not in inputs],
        "all_six_markets_supplied": complete,
        "common_t0": next(iter(timestamps)) if complete and len(timestamps) == 1 else None,
        "forecast_mutated": False, "source_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE",
        "calendar_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE", "predictive_quality": "NOT_MEASURED",
        "portfolio_direction": None, "current_quotes_claimed": False}
    return {"report_id": "six_market_" + hashlib.sha256(canonical_bytes(body)).hexdigest(), **body}


def _safe(value):
    return "нет данных" if value is None else str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def render_six_market_report(report):
    mode = "Повтор на сохранённых исследовательских данных; это не новый прогноз и не текущие котировки." if report["evidence_mode"] == "OFFLINE_RESEARCH_REPEAT" else "Просмотр сохранённых записей; подлинность их выпуска этим отчётом не удостоверяется."
    lines = ["# Сводка шести рынков", "", mode,
        "Число свечей взято из исходного снимка. Наличие свечей и допуск показателя показаны отдельно.",
        "", "| Рынок | Точный инструмент | Запись прогноза | Свечей: день / час / 15 минут | Исходное решение | Поздние свечи |",
        "|---|---|---|---|---|---|"]
    for row in report["rows"]:
        if row["input_status"] == "NOT_SUPPLIED":
            lines.append("| " + row["market"] + " | нет исходных файлов | нет данных | нет данных | нет данных | нет данных |")
            continue
        decision = "отказ от направления" if row["decision_status"] == "ABSTAIN" else "нейтральная оценка" if row["decision_status"] == "BASELINE_NEUTRAL" else {"UP": "предварительная оценка роста", "DOWN": "предварительная оценка снижения"}[row["direction"]]
        later = row["later_observation"]
        text = "не переданы" if later["status"] == "NOT_SUPPLIED" else str(later["supplied_bar_count"]) + "; только переданные свечи"
        lines.append("| " + " | ".join((_safe(row["market"]), _safe(row["secid"]), _safe(row["t0"]),
            " / ".join(_safe(tf["actual_candles"]) for tf in row["timeframes"]), decision, text)) + " |")
    if report["common_t0"] is None:
        lines.extend(["", "Единый момент записи всех шести рынков не установлен; время каждой записи сохранено отдельно."])
    labels = {"D1": "День", "H1": "Час", "M15": "15 минут"}
    for row in report["rows"]:
        lines.extend(["", "## " + row["market"], ""])
        if row["input_status"] == "NOT_SUPPLIED":
            lines.append("Исходные файлы этого рынка не переданы. Это не доказательство отсутствия данных на бирже.")
            continue
        if row["abstention_reasons"]:
            lines.append("Причины отказа именно в сохранённой записи:")
            lines.extend("- " + item["explanation_ru"] for item in row["abstention_reasons"])
        else:
            lines.append("В сохранённой записи нет отказа из-за непригодных обязательных данных.")
        lines.extend(["", "| Период | Изменение за 5 шагов | За 10 шагов | За 20 шагов |",
            "|---|---|---|---|"])
        for tf in row["timeframes"]:
            values = []
            for window in tf["windows"]:
                if window["status"] == "AVAILABLE": value = "допущено"
                elif window["status"] == "INSUFFICIENT_HISTORY": value = "мало свечей; нужно " + str(window["required_completed_candles"])
                else: value = "источник или показатель не допущен; причина в подробном отчёте"
                values.append(value)
            lines.append("| " + " | ".join([labels[tf["timeframe"]], *values]) + " |")
        reference = row["reference"]
        lines.append("Опорная цена: " + _safe(reference["price"]) + ("; источник допущен." if reference["status"] == "AVAILABLE" else "; источник не допущен."))
        later = row["later_observation"]
        if later["status"] == "NOT_SUPPLIED":
            lines.append("Последующие свечи не переданы. Это не проверка будущего исхода и не свидетельство сбоя сбора.")
        else:
            lines.append("Поздние наблюдения до: " + _safe(later["observation_at"]) + ".")
            continuity = later["schedule_continuity"]
            messages = {"EXPECTED_SCHEDULE_MISSING": "Расписание ожидаемых свечей не передано; серии закрытий недоступны.",
                "MISSING_EXPECTED_BARS": "Есть пропуски в объявленном расписании; серии закрытий недоступны.",
                "UNEXPECTED_BARS": "Есть свечи вне объявленного расписания; серии закрытий недоступны."}
            lines.append(messages.get(continuity["reason"], "Совпадение с объявленным расписанием проверено; его подлинность отдельно не подтверждена."))
    lines.extend(["", "Число свечей не доказывает независимую календарную полноту или время получения данных.",
        "Отказ отличается от нейтральной оценки. Отсутствие поздних входов отличается от пропуска ожидаемых свечей.",
        "Прогнозы не пересчитаны. Точность, совокупный торговый сигнал и готовность рабочей установки здесь не оцениваются."])
    return "\n".join(lines)
