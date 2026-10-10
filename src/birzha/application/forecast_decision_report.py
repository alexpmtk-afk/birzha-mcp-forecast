"""Explain an already frozen V3 decision; never rebuild the forecast."""
from copy import deepcopy
import hashlib
import re
from birzha.application.level_reaction import canonical_bytes
from birzha.domain.forecast import LEVEL_EVIDENCE_RECORD_VERSION, validate_baseline_evidence_payload

DECISION_REPORT_VERSION = "FROZEN_DECISION_EXPLANATION_V1"
DIRECT_REASONS = {
    "DATA_QUALITY_CONTRACT_MISSING": "Нет записи, подтверждающей качество исходных данных.",
    "CAUSAL_REFERENCE_PRICE_UNAVAILABLE": "Нет пригодной опорной цены, доступной к моменту записи прогноза.",
    "D1_POSITIVE_ATR_PRICE_SCALE_UNAVAILABLE": "Нет положительного дневного масштаба колебаний цены.",
    "D1_RAW_ATR_INPUT_UNAVAILABLE": "Нет пригодной дневной цены или положительного дневного показателя колебаний.",
    "D1_BASELINE_SCORE_UNAVAILABLE": "Нет пригодной дневной оценки направления.",
    "D1_QUALITY_COUNT_CONFLICT": "Число дневных свечей расходится с записью о качестве данных либо эта запись неоднозначна.",
}
FEATURE_NAMES = {
    "d1_atr_price_scale": "Дневной масштаб колебаний цены",
    "d1_return_5_atr": "Изменение цены за 5 дневных шагов относительно дневных колебаний",
    "d1_return_20_atr": "Изменение цены за 20 дневных шагов относительно дневных колебаний",
    "d1_efficiency_20": "Эффективность движения за 20 дневных шагов",
}


def explain_abstention_reason(code):
    """Translate only known reason grammar; retain unknown codes without guessing."""
    text = DIRECT_REASONS.get(code)
    if text is not None:
        return {"code": code, "known": True, "explanation_ru": text}
    parts = code.split(":")
    if len(parts) == 3 and parts[0] in FEATURE_NAMES:
        feature, status, reason = parts
        detail = None
        match = re.fullmatch(r"D1_requires_([1-9][0-9]{0,3})_candles", reason)
        if status == "INSUFFICIENT_HISTORY" and match:
            detail = f"недостаточно завершённых дневных свечей; требуется {int(match[1])}."
        elif status == "UNAVAILABLE":
            detail = {
                "D1_quality_not_proven_for_feature": "качество дневных данных для этого показателя не подтверждено.",
                "causal_value_not_available_at_snapshot_T0": "значение недоступно в исходном снимке при записи прогноза.",
                "nonfinite_or_invalid_numeric_value": "значение не является пригодным конечным числом.",
            }.get(reason)
        if detail:
            return {"code": code, "known": True, "explanation_ru": FEATURE_NAMES[feature] + ": " + detail}
    return {"code": code, "known": False,
            "explanation_ru": "В исходной записи указана причина, для которой ещё нет подтверждённого пояснения. Она сохранена без догадок."}


def build_forecast_decision_report(forecast_payload):
    if forecast_payload.get("record_version") != LEVEL_EVIDENCE_RECORD_VERSION:
        raise ValueError("decision explanation requires a frozen V3 forecast")
    validate_baseline_evidence_payload(forecast_payload)
    for name in ("forecast_id", "engine_version", "created_at_t0", "snapshot_id", "snapshot_contract_version", "symbol", "secid"):
        if not isinstance(forecast_payload.get(name), str) or not forecast_payload[name]:
            raise ValueError("frozen decision identity/version missing: " + name)
    for name in ("reasons", "warnings"):
        values = forecast_payload.get(name)
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError("frozen decision diagnostics must be string lists")
    excluded = []
    labels = {"D1": "Дневной период", "H1": "Часовой период", "M15": "Пятнадцатиминутный период"}
    for reason in forecast_payload["reasons"]:
        if reason.startswith("optional_timeframe_excluded:") and reason.split(":",1)[1] in labels:
            tf = reason.split(":",1)[1]
            excluded.append({"code": reason, "component": tf,
                "explanation_ru": labels[tf] + " исключён из исходной оценки; запись не уточняет отдельную причину исключения."})
        elif reason == "optional_profile_excluded":
            excluded.append({"code": reason, "component": "PROFILE",
                "explanation_ru": "Профиль распределения объёма исключён из исходной оценки; запись не уточняет отдельную причину исключения."})
    body = {"version": DECISION_REPORT_VERSION,
        **{key: forecast_payload[key] for key in ("forecast_id", "record_version", "engine_version", "snapshot_id", "snapshot_contract_version", "symbol", "secid", "created_at_t0", "decision_status", "direction", "signal_strength", "validation_status")},
        "forecast_sha256": hashlib.sha256(canonical_bytes(forecast_payload)).hexdigest(),
        "horizons": deepcopy(forecast_payload["horizons"]),
        "field_availability": deepcopy(forecast_payload["field_availability"]),
        "abstention_reasons": [explain_abstention_reason(code) for code in forecast_payload["abstention_reasons"]],
        "excluded_components": excluded, "source_reasons": list(forecast_payload["reasons"]),
        "source_warnings": list(forecast_payload["warnings"]),
        "decision_origin": "FROZEN_RECORD_NOT_RECOMPUTED", "forecast_mutated": False,
        "issuer_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE", "probability": None,
        "predictive_quality": "NOT_MEASURED"}
    return {"report_id": "frozen_decision_" + hashlib.sha256(canonical_bytes(body)).hexdigest(), **body}


def render_forecast_decision_report(report):
    decision = report["decision_status"]
    if decision == "ABSTAIN":
        result = "Отказ от выбора направления: обязательные данные не допущены. Это не прогноз бокового движения."
    elif decision == "BASELINE_NEUTRAL":
        result = "В исходной записи выбрана нейтральная оценка. Это отдельное решение модели, а не отказ из-за непригодных обязательных данных."
    else:
        direction = {"UP": "рост", "DOWN": "снижение"}[report["direction"]]
        result = "В исходной записи выбрана предварительная оценка: " + direction + "."
    lines = ["## Исходное решение прогноза", "", result,
        "Решение прочитано из сохранённой записи; сейчас оно не пересчитывалось.",
        "Горизонты записи: " + " / ".join(str(h["sessions"]) for h in report["horizons"]) + " торговых сессий."]
    if decision == "ABSTAIN":
        lines.extend(["", "### Почему направление не выбрано", ""])
        lines.extend("- " + item["explanation_ru"] for item in report["abstention_reasons"])
    else:
        lines.append("Причин отказа в исходной записи нет.")
        lines.append(f"Внутренний коэффициент выраженности оценки: {report['signal_strength']}. Это не вероятность успеха и не подтверждённая точность.")
    if report["excluded_components"]:
        lines.extend(["", "### Что исключено из исходной оценки", ""])
        lines.extend("- " + item["explanation_ru"] for item in report["excluded_components"])
        lines.append("Исключение отдельного компонента само по себе не означает отказ от всего прогноза.")
    lines.extend(["", "Полный контроль, маршрут и вероятность успеха не определены. Прогноз остаётся непроверенной исходной моделью.",
        "Совпадение содержания не удостоверяет автора или время выпуска записи; будущая точность здесь не оценивается."])
    return "\n".join(lines)
