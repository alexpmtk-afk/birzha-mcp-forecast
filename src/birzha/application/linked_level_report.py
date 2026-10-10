"""A read-only view of T0 level facts and explicitly later observations."""
import hashlib
from birzha.application.price_location import build_price_location_report
from birzha.application.level_reaction import build_level_reaction_report, canonical_bytes
from birzha.application.level_holding import build_level_holding_report
from birzha.domain.forecast import LEVEL_EVIDENCE_RECORD_VERSION, validate_baseline_evidence_payload
from birzha.domain.snapshot import market_snapshot_id

LINKED_LEVEL_REPORT_VERSION = "LINKED_LEVEL_FACTS_V1"
LEVEL_NAMES_RU = (
    "День: нижняя граница", "День: верхняя граница",
    "Час: нижняя граница", "Час: верхняя граница",
    "15 минут: нижняя граница", "15 минут: верхняя граница",
    "Профиль: нижняя граница", "Профиль: наибольший объём", "Профиль: верхняя граница",
)


def build_linked_level_report(snapshot, forecast_payload, *, series=None, observation_at=None, schedule_bytes=None):
    """Recompute facts from bound source inputs; never consume unchecked reports.

    This verifies content consistency, not issuer signatures or source receipt
    authenticity. Later observations cannot modify or enrich the T0 snapshot.
    """
    if forecast_payload.get("record_version") != LEVEL_EVIDENCE_RECORD_VERSION:
        raise ValueError("linked report requires a frozen V3 forecast")
    validate_baseline_evidence_payload(forecast_payload)
    if not isinstance(forecast_payload.get("forecast_id"), str) or not forecast_payload["forecast_id"]:
        raise ValueError("forecast identity missing")
    expected = {"snapshot_id": market_snapshot_id(snapshot), "symbol": snapshot.symbol,
                "secid": snapshot.secid, "created_at_t0": snapshot.as_of,
                "snapshot_contract_version": snapshot.contract_version}
    if any(forecast_payload.get(key) != value for key, value in expected.items()):
        raise ValueError("snapshot/forecast identity, instrument, T0 or version mismatch")
    location = build_price_location_report(snapshot)
    levels = [item["origin"] for item in location["levels"]]
    if canonical_bytes(forecast_payload["level_evidence"]) != canonical_bytes(levels):
        raise ValueError("frozen levels differ from their supplied snapshot origins")
    if location["reference"]["price"] is not None and canonical_bytes(forecast_payload.get("reference_price")) != canonical_bytes(location["reference"]["price"]):
        raise ValueError("frozen reference price conflicts with admitted snapshot price")
    reaction = holding = None
    if series is None:
        if observation_at is not None or schedule_bytes is not None:
            raise ValueError("observation time or schedule requires a candle series")
    else:
        if observation_at is None:
            raise ValueError("later observations require an explicit cutoff")
        reaction = build_level_reaction_report(forecast_payload, series, observation_at=observation_at)
        holding = build_level_holding_report(forecast_payload, series, observation_at=observation_at, schedule_bytes=schedule_bytes)
        if holding["reaction_report_id"] != reaction["report_id"]:
            raise ValueError("reaction and holding reports disagree")
    rows = []
    for index, item in enumerate(location["levels"]):
        observed = reaction["levels"][index] if reaction else None
        held = holding["levels"][index] if holding else None
        rows.append({"level_name": item["origin"]["name"], "origin": item["origin"],
                     "at_t0": {key: value for key, value in item.items() if key != "origin"},
                     "after_t0": None if observed is None else {
                         "reaction_status": observed["status"], "reaction_reason": observed["reason"],
                         "supplied_bar_count": reaction["supplied_bar_count"],
                         "range_contact_bars": observed["range_contact_bars"],
                         "close_counts": observed["close_counts"], "close_side_changes": observed["close_side_changes"],
                         "returns_to_initial_close_side": observed["returns_to_initial_close_side"],
                         "holding_status": held["status"], "holding_reason": held["reason"],
                         "longest_close_runs": held["longest_close_runs"], "trailing_close_run": held["trailing_close_run"]}})
    body = {"version": LINKED_LEVEL_REPORT_VERSION, "forecast_id": forecast_payload["forecast_id"],
            "forecast_sha256": hashlib.sha256(canonical_bytes(forecast_payload)).hexdigest(),
            **expected, "snapshot_sha256": location["snapshot_sha256"], "rows": rows,
            "at_t0": location, "after_t0": None if reaction is None else {"observation_at": observation_at,
                "timeframe": series.timeframe, "reaction": reaction, "holding": holding},
            "binding_status": "CONTENT_AND_LEVEL_ORIGINS_MATCH",
            "source_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE",
            "forecast_mutated": False, "predictive_quality": "NOT_MEASURED",
            "full_location": None, "control": "UNKNOWN", "route": "UNAVAILABLE"}
    return {"report_id": "linked_levels_" + hashlib.sha256(canonical_bytes(body)).hexdigest(), **body}


def _text(value):
    return "нет данных" if value is None else str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")


def _availability(row):
    status = row["origin"]["status"]
    if status == "AVAILABLE": return "допущен"
    if status == "AVAILABLE_APPROXIMATE": return "приблизительный"
    if status == "INSUFFICIENT_HISTORY": return "недостаточно завершённых свечей"
    return "источник не допущен"


def render_linked_level_report(report):
    location = report["at_t0"]
    reference = location["reference"]
    lines = ["# Цена и уровни", "", f"Инструмент: {_text(report['symbol'])}; точный код: {_text(report['secid'])}.",
             f"Прогноз записан: {_text(report['created_at_t0'])}.", "", "## При записи прогноза", "",
             f"Опорная цена: {_text(reference['price'])}."]
    if reference["price"] is None:
        lines.append("Выбранный источник цены не допущен; расстояния не рассчитываются.")
    else:
        period = {"D1": "день", "H1": "час", "M15": "15 минут"}[reference["timeframe"]]
        lines.append(f"Период цены: {period}; свеча завершена: {_text(reference['source_end'])}.")
        for key, label in (("nearest_above", "Ближайший уровень выше"), ("nearest_below", "Ближайший уровень ниже")):
            nearest = location[key]
            lines.append(label + (": среди допущенных уровней отсутствует." if nearest is None else f": {_text(nearest['price'])}; расстояние: {_text(nearest['absolute_distance_price'])}; источников: {len(nearest['origins'])}."))
    lines.extend(["", "| Источник уровня | Цена | Допуск | Уровень относительно цены | Расстояние в цене со знаком |",
                  "|---|---:|---|---|---:|"])
    for label, row in zip(LEVEL_NAMES_RU, report["rows"]):
        facts=row["at_t0"]
        side={"ABOVE":"выше","BELOW":"ниже","ON":"точное совпадение",None:"нет данных"}[facts["side"]]
        lines.append("| " + " | ".join((label, _text(row["origin"]["price"]), _availability(row), side, _text(facts["signed_distance_price"]))) + " |")
    lines.extend(["", "Расстояние со знаком: уровень минус опорная цена. Точное совпадение не означает близость с допуском.", "", "## После записи прогноза", ""])
    later=report["after_t0"]
    if later is None:
        lines.append("Последующие свечи не переданы; реакции и серии закрытий не рассчитаны.")
    else:
        lines.append(f"Наблюдения до: {_text(later['observation_at'])}. Эти данные не были входом прежнего прогноза.")
        continuity=later["holding"]["continuity"]
        messages={"EXPECTED_SCHEDULE_MISSING":"Расписание ожидаемых свечей не передано; серии закрытий недоступны.",
                  "MISSING_EXPECTED_BARS":"Есть пропуски ожидаемых свечей; серии закрытий недоступны.",
                  "UNEXPECTED_BARS":"Есть свечи вне ожидаемого расписания; серии закрытий недоступны."}
        lines.append(messages.get(continuity["reason"], "Переданные свечи совпадают с объявленным расписанием. Подлинность расписания требует отдельной проверки."))
        lines.extend(["", "Касания и смены стороны ниже относятся только к переданным свечам.", "",
                      "| Источник уровня | Свечей с уровнем внутри диапазона | Смен стороны закрытия | Возвратов | Самая длинная серия закрытий: выше / на / ниже |",
                      "|---|---:|---:|---:|---|"])
        for label,row in zip(LEVEL_NAMES_RU,report["rows"]):
            facts=row["after_t0"]; runs=facts["longest_close_runs"]
            run_text="нет данных" if runs is None else " / ".join(str(runs[side]) for side in ("ABOVE","ON","BELOW"))
            lines.append("| " + " | ".join((label,_text(facts['range_contact_bars']),_text(facts['close_side_changes']),_text(facts['returns_to_initial_close_side']),run_text)) + " |")
    lines.extend(["", "## Границы результата", "", "Сила уровня, закрепление, полный контроль и торговые цели не определены.",
                  "Время завершения снимка не подтверждает время его получения. Этот отчёт проверяет согласованность содержания, а не подлинность источника.",
                  "Точность прогноза здесь не измеряется; исходный прогноз и снимок не изменены."])
    return "\n".join(lines)
