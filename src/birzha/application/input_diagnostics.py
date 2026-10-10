"""Measured source age, explicit admission reasons and level provenance only."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import re
from birzha.application.level_reaction import canonical_bytes
from birzha.application.price_location import build_price_location_report
from birzha.application.timeframe_movement import build_timeframe_movement_report

INPUT_DIAGNOSTICS_VERSION = "MARKET_INPUT_DIAGNOSTICS_V1"
REASONS_RU = {
    "SNAPSHOT_VERSION_UNPROVEN": "Версия исходного снимка не подтверждена.",
    "FULL_WINDOW_PRODUCER_VERSION_UNPROVEN": "Не подтверждена версия расчёта полных окон.",
    "QUALITY_COUNT_OR_TIMEFRAME_CONFLICT": "Период или число свечей расходится с записью о качестве либо запись неоднозначна.",
    "SOURCE_QUALITY_UNPROVEN": "Качество источника для этого расчёта не подтверждено.",
    "SOURCE_AFTER_T0": "Последняя свеча завершена позже момента записи прогноза.",
    "SOURCE_TIME_UNAVAILABLE": "Время завершения последней свечи неизвестно или некорректно.",
    "SOURCE_TIMEZONE_UNSPECIFIED": "У времени завершения свечи не указан часовой пояс; давность не вычисляется.",
    "T0_TIME_UNAVAILABLE": "Момент записи прогноза неизвестен или некорректен.",
    "T0_TIMEZONE_UNSPECIFIED": "У момента записи прогноза не указан часовой пояс; давность не вычисляется.",
    "RETURN_VALUE_UNAVAILABLE": "Нет пригодного изменения цены за полное окно.",
    "POSITIVE_LAST_CLOSE_REQUIRED_FOR_RETURN_DIRECTION": "Для определения направления по этой формуле нужна положительная последняя цена.",
    "POSITIVE_RETURN_BASE_UNPROVEN": "Не подтверждена положительная начальная цена для этой формулы изменения.",
    "REFERENCE_PRICE_MISSING": "В снимке нет опорной цены.",
    "REFERENCE_PRICE_INVALID": "Выбранная опорная цена не является пригодным конечным числом.",
    "NORMALIZED_REFERENCE_CONFLICT": "Опорная цена расходится с нормализованным показателем либо тот отсутствует.",
    "ATR_SCALE_UNAVAILABLE": "Нет пригодного положительного дневного масштаба колебаний.",
    "NORMALIZED_ATR_CONFLICT": "Дневной масштаб колебаний расходится с нормализованным показателем.",
    "FULL_RANGE_UNAVAILABLE_OR_REVERSED": "Границы полного диапазона отсутствуют, некорректны либо перепутаны местами.",
    "PROFILE_MISSING": "Профиль распределения объёма не передан.",
    "PROFILE_GEOMETRY_OR_VOLUME_INVALID": "Границы профиля или его общий объём некорректны.",
    "PROFILE_NORMALIZED_ADMISSION_MISSING": "Не подтверждён допуск нормализованных показателей профиля.",
    "EXACT_PROFILE_SOURCE_UNPROVEN": "Точный источник профиля, инструмент или его качество не подтверждены.",
    "PROFILE_METHOD_UNSUPPORTED_OR_PRECISION_CONFLICT": "Метод профиля не поддержан либо противоречит объявленной точности.",
    "CANDLE_VOLUME_PROXY": "Профиль приблизительный: объём распределён по данным свечей, а не точных сделок.",
}


def explain_input_reason(code):
    if code is None:
        return {"code": None, "known": True, "explanation_ru": "Причина недопуска отсутствует."}
    text = REASONS_RU.get(code)
    if text is None:
        match = re.fullmatch(r"REQUIRES_([1-9][0-9]{0,3})_COMPLETED_CANDLES", code)
        if match:
            text = f"Недостаточно завершённых свечей; для этого расчёта требуется {int(match[1])}."
    return {"code": code, "known": text is not None,
        "explanation_ru": text or "Причина сохранена в исходном расчёте, но её смысл пока не объяснён; вывод не придумывается."}


def _explicit_time(value, prefix):
    if not isinstance(value, str):
        return None, prefix + "_TIME_UNAVAILABLE"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None, prefix + "_TIMEZONE_UNSPECIFIED"
        return parsed.astimezone(timezone.utc), None
    except (ValueError, OverflowError):
        return None, prefix + "_TIME_UNAVAILABLE"


def measure_source_age(source_end, t0):
    """Elapsed wall time only, with explicit zones; never receipt or staleness."""
    anchor, reason = _explicit_time(t0, "T0")
    if reason is None:
        end, reason = _explicit_time(source_end, "SOURCE")
        if reason is None and end > anchor:
            reason = "SOURCE_AFTER_T0"
    seconds = (anchor - end).total_seconds() if reason is None else None
    return {"status": "MEASURED" if reason is None else "UNAVAILABLE", "source_end": source_end,
        "t0": t0, "elapsed_wall_seconds": seconds, "elapsed_wall_hours": seconds / 3600 if seconds is not None else None,
        "reason": explain_input_reason(reason), "stale": None,
        "trading_session_gap": None, "receipt_age": None}


def build_market_input_diagnostics(snapshot):
    movement = build_timeframe_movement_report(snapshot)
    location = build_price_location_report(snapshot)
    timeframes = []
    for tf in ("D1", "H1", "M15"):
        state = getattr(snapshot, tf.lower())
        quality = [q for q in snapshot.quality_contract.timeframes if q.timeframe == tf] if snapshot.quality_contract else []
        binding = len(quality) == 1 and quality[0].candles == state.candles and state.timeframe == tf
        declared_end = quality[0].latest_completed_end if len(quality) == 1 else None
        age = measure_source_age(declared_end, snapshot.as_of)
        if not binding:
            age.update(status="UNAVAILABLE",elapsed_wall_seconds=None,elapsed_wall_hours=None,
                reason=explain_input_reason("QUALITY_COUNT_OR_TIMEFRAME_CONFLICT"))
        metrics = [next(m for m in w["metrics"] if m["timeframe"] == tf) for w in movement["windows"]]
        timeframes.append({"timeframe": tf, "actual_candles": state.candles,
            "source_time_origin": "DECLARED_QUALITY_CONTRACT", "quality_binding_matches": binding,
            "quality_status": quality[0].status if len(quality) == 1 else None, "age": age,
            "windows": [{**deepcopy(m), "reason_explained": explain_input_reason(m["reason"])} for m in metrics]})
    levels = [{"origin": deepcopy(row["origin"]), "reason_explained": explain_input_reason(row["origin"]["reason"]),
        "precision": "APPROXIMATE" if row["origin"]["status"] == "AVAILABLE_APPROXIMATE" else "DECLARED_METHOD" if row["origin"]["status"] == "AVAILABLE" else None,
        "source_age": measure_source_age(row["origin"]["source_end"], snapshot.as_of)} for row in location["levels"]]
    body = {"version": INPUT_DIAGNOSTICS_VERSION, "snapshot_id": movement["snapshot_id"],
        "snapshot_sha256": movement["snapshot_sha256"], "snapshot_contract_version": movement["snapshot_contract_version"],
        "symbol": snapshot.symbol, "secid": snapshot.secid, "t0": snapshot.as_of,
        "timeframes": timeframes, "reference": {**deepcopy(location["reference"]),
            "reason_explained": explain_input_reason(location["reference"]["reason"])},
        "atr_scale": {**deepcopy(location["atr_scale"]),"reason_explained": explain_input_reason(location["atr_scale"]["reason"])},
        "levels": levels, "movement_report_id": movement["report_id"], "location_report_id": location["report_id"],
        "source_receipt_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE",
        "calendar_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE", "forecast_mutated": False,
        "predictive_quality": "NOT_MEASURED"}
    return {"report_id": "input_diagnostics_" + hashlib.sha256(canonical_bytes(body)).hexdigest(), **body}


def _safe(value):
    return "нет данных" if value is None else str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def render_market_input_diagnostics(report):
    periods = {"D1": "День", "H1": "Час", "M15": "15 минут", None: "не указан"}
    lines = ["### Давность данных при записи прогноза", "",
        "| Период | Завершение последней свечи по записи качества | Часов до записи прогноза | Пояснение |",
        "|---|---|---:|---|"]
    for tf in report["timeframes"]:
        age = tf["age"]
        hours = _safe(age["elapsed_wall_hours"])
        text = "измерено обычное прошедшее время" if age["status"] == "MEASURED" else age["reason"]["explanation_ru"]
        lines.append("| " + " | ".join((periods[tf["timeframe"]],_safe(age["source_end"]),hours,text)) + " |")
    lines.extend(["", "Это не время получения данных и не число пропущенных торговых сессий. Порог устаревания не назначен.",
        "", "### Причины недоступности расчётов", ""])
    issues = []
    for tf in report["timeframes"]:
        for window in tf["windows"]:
            if window["status"] != "AVAILABLE":
                issues.append(periods[tf["timeframe"]] + ", изменение за " + str(window["steps"]) + " шагов: " + window["reason_explained"]["explanation_ru"])
    for key,label in (("reference","Опорная цена"),("atr_scale","Дневной масштаб колебаний")):
        if report[key]["status"] != "AVAILABLE":
            issues.append(label + ": " + report[key]["reason_explained"]["explanation_ru"])
    lines.extend(["- " + text for text in issues] or ["Окна изменений, опорная цена и дневной масштаб колебаний допущены по исходному снимку."])
    lines.extend(["", "### Происхождение девяти уровней", "",
        "| Уровень | Цена | Метод | Период | Время источника | Допуск / причина |",
        "|---|---:|---|---|---|---|"])
    names = ("День: нижняя граница", "День: верхняя граница", "Час: нижняя граница", "Час: верхняя граница",
        "15 минут: нижняя граница", "15 минут: верхняя граница", "Профиль: нижняя граница", "Профиль: наибольший объём", "Профиль: верхняя граница")
    methods = {"COMPLETED_HLC_RANGE_20_V1":"границы 20 завершённых свечей", "PUBLIC_TRADES_PRICE_QUANTITY_V1":"точные цены и объёмы сделок", "CANDLE_VOLUME_PROXY_V1":"приближение по объёмам свечей", "CANDLE_TYPICAL_PRICE_VOLUME_PROXY_V1":"приближение по типичным ценам и объёмам свечей", "UNAVAILABLE":"метод отсутствует"}
    for name,level in zip(names,report["levels"]):
        origin = level["origin"]
        status = "допущен" if origin["status"] == "AVAILABLE" else "приблизительный; " + level["reason_explained"]["explanation_ru"] if origin["status"] == "AVAILABLE_APPROXIMATE" else "не допущен; " + level["reason_explained"]["explanation_ru"]
        lines.append("| " + " | ".join((name,_safe(origin["price"]),methods.get(origin["method"],"метод сохранён, пояснение пока отсутствует"),periods[origin["timeframe"]],_safe(origin["source_end"]),status)) + " |")
    lines.extend(["", "Все уровни связаны с тем же точным инструментом, моментом записи и исходным снимком. Совпавшие цены сохраняют разные источники.",
        "Приблизительный профиль не становится точным; допуск уровня не доказывает его силу или контроль цены."])
    return "\n".join(lines)
