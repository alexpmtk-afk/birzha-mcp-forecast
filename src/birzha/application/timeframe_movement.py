"""Sign comparisons of admitted full-window returns, never full ALIGNMENT."""
import hashlib
from itertools import combinations
from birzha.application.features import _finite_price
from birzha.application.price_levels import _quality
from birzha.application.level_reaction import canonical_bytes
from birzha.domain.snapshot import market_snapshot_id, MARKET_SNAPSHOT_CONTRACT_VERSION

MOVEMENT_FACTS_VERSION = "TIMEFRAME_MOVEMENT_FACTS_V1"
TIMEFRAMES = ("D1", "H1", "M15")
STEPS = (5, 10, 20)


def _metric(snapshot, tf, steps):
    state = getattr(snapshot, tf.lower())
    status, reason, end = _quality(snapshot, tf, steps + 1)
    value = getattr(state, f"return_{steps}")
    if snapshot.contract_version != MARKET_SNAPSHOT_CONTRACT_VERSION:
        status, reason = "UNAVAILABLE", "SNAPSHOT_VERSION_UNPROVEN"
    if status == "AVAILABLE":
        if not _finite_price(value):
            status, reason = "UNAVAILABLE", "RETURN_VALUE_UNAVAILABLE"
        elif not _finite_price(state.last_close) or state.last_close <= 0:
            status, reason = "UNAVAILABLE", "POSITIVE_LAST_CLOSE_REQUIRED_FOR_RETURN_DIRECTION"
        elif value <= -1:
            status, reason = "UNAVAILABLE", "POSITIVE_RETURN_BASE_UNPROVEN"
    ready = status == "AVAILABLE"
    return {"timeframe": tf, "steps": steps, "required_completed_candles": steps + 1,
            "actual_candles": state.candles, "source_end": end, "last_close": state.last_close,
            "status": status, "reason": reason, "return_fraction": value if ready else None,
            "direction": ("UP" if value > 0 else "DOWN" if value < 0 else "EXACTLY_FLAT") if ready else None}


def _pair(left, right):
    ready = left["status"] == right["status"] == "AVAILABLE"
    relation = None
    if ready:
        a, b = left["direction"], right["direction"]
        relation = "SAME_SIGN" if a == b else "ONE_EXACTLY_FLAT" if "EXACTLY_FLAT" in (a, b) else "OPPOSITE_SIGNS"
    return {"left": left["timeframe"], "right": right["timeframe"],
            "status": "AVAILABLE" if ready else "UNAVAILABLE", "relation": relation,
            "unavailable_timeframes": [i["timeframe"] for i in (left, right) if i["status"] != "AVAILABLE"]}


def build_timeframe_movement_report(snapshot):
    windows = []
    for steps in STEPS:
        metrics = [_metric(snapshot, tf, steps) for tf in TIMEFRAMES]
        ready = all(i["status"] == "AVAILABLE" for i in metrics)
        counts = {direction: sum(i["direction"] == direction for i in metrics) for direction in ("UP", "DOWN", "EXACTLY_FLAT")} if ready else None
        windows.append({"steps": steps, "metrics": metrics,
                        "pairs": [_pair(a, b) for a, b in combinations(metrics, 2)],
                        "all_timeframes_available": ready, "complete_direction_counts": counts,
                        "all_same_nonzero_direction": bool(counts and (counts["UP"] == 3 or counts["DOWN"] == 3)) if ready else None,
                        "opposite_nonzero_directions_present": bool(counts["UP"] and counts["DOWN"]) if ready else None,
                        "all_exactly_flat": counts["EXACTLY_FLAT"] == 3 if ready else None})
    body = {"version": MOVEMENT_FACTS_VERSION, "snapshot_id": market_snapshot_id(snapshot),
            "snapshot_sha256": hashlib.sha256(canonical_bytes(snapshot.to_dict())).hexdigest(),
            "snapshot_contract_version": snapshot.contract_version,
            "symbol": snapshot.symbol, "secid": snapshot.secid, "t0": snapshot.as_of,
            "windows": windows, "comparison_basis": "SAME_BAR_STEP_COUNT_DIFFERENT_ELAPSED_DURATIONS",
            "return_formula": "LAST_DIVIDED_BY_N_STEPS_AGO_MINUS_ONE", "flat_rule": "EXACT_ZERO_NO_TOLERANCE",
            "window_continuity": "NOT_INDEPENDENTLY_ATTESTED", "source_receipt_authentication": "NOT_ESTABLISHED_BY_THIS_MODULE",
            "alignment_score": None, "full_alignment": None, "control": "UNKNOWN", "predictive_quality": "NOT_MEASURED"}
    return {"report_id": "movement_" + hashlib.sha256(canonical_bytes(body)).hexdigest(), **body}


def _safe(value):
    return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")


def render_timeframe_movement_report(report):
    names = {"D1": "День", "H1": "Час", "M15": "15 минут"}
    directions = {"UP": "рост", "DOWN": "снижение", "EXACTLY_FLAT": "точно без изменения", None: "нет данных"}
    lines = ["# Сравнение движения цены", "", f"Инструмент: {_safe(report['symbol'])}; точный код: {_safe(report['secid'])}; снимок: {_safe(report['t0'])}.",
             "", "Сравнивается одинаковое число шагов каждого периода. Пять дневных свечей и пять часовых охватывают разное время."]
    for window in report["windows"]:
        lines.extend(["", f"## Изменение за {window['steps']} шагов", "", "| Период | Последняя завершённая свеча | Изменение, доля | Направление | Причина недоступности |", "|---|---|---:|---|---|"])
        for metric in window["metrics"]:
            reasons = {"RETURN_VALUE_UNAVAILABLE": "нет пригодного изменения за полное окно",
                       "SNAPSHOT_VERSION_UNPROVEN": "версия исходного снимка не подтверждена",
                       "POSITIVE_LAST_CLOSE_REQUIRED_FOR_RETURN_DIRECTION": "для направления этой формулы нужна положительная последняя цена",
                       "POSITIVE_RETURN_BASE_UNPROVEN": "положительная начальная цена не подтверждена",
                       "SOURCE_AFTER_T0": "свеча завершена позже снимка", "SOURCE_TIME_UNAVAILABLE": "время завершения не подтверждено",
                       "QUALITY_COUNT_OR_TIMEFRAME_CONFLICT": "период или число свечей расходятся с проверкой качества",
                       "SOURCE_QUALITY_UNPROVEN": "качество источника не подтверждено",
                       "FULL_WINDOW_PRODUCER_VERSION_UNPROVEN": "версия расчёта полных окон не подтверждена"}
            reason = "недостаточно завершённых свечей" if metric["status"] == "INSUFFICIENT_HISTORY" else reasons.get(metric["reason"], "—")
            value = str(metric["return_fraction"]) if metric["return_fraction"] is not None else "нет данных"
            lines.append("| " + " | ".join((names[metric["timeframe"]], _safe(metric["source_end"]) if metric["source_end"] is not None else "нет данных", value, directions[metric["direction"]], reason)) + " |")
        for pair in window["pairs"]:
            text = {"SAME_SIGN": "знаки совпадают", "OPPOSITE_SIGNS": "противоположные направления", "ONE_EXACTLY_FLAT": "один период точно без изменения", None: "сравнение недоступно"}[pair["relation"]]
            lines.append(f"{names[pair['left']]} и {names[pair['right']]}: {text}.")
        if not window["all_timeframes_available"]:
            lines.append("Общее сравнение всех трёх периодов недоступно; отдельные пригодные пары показаны выше.")
    lines.extend(["", "Нулевое изменение не заменяет пропущенные данные. Разное время последних свечей сохранено явно.",
                  "Баллы согласованности, полный контроль и торговый сигнал здесь не рассчитываются. Точность прогноза не измеряется.",
                  "Время завершения не удостоверяет получение данных; независимая календарная полнота не подтверждена."])
    return "\n".join(lines)
