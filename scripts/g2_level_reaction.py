"""Observe a frozen forecast against explicit later candle receipt evidence."""
import argparse
import json
from pathlib import Path
from birzha.application.level_reaction import build_level_reaction_report
from birzha.domain.market import Instrument, Candle, CandleSeries
from birzha.storage.level_reaction import LevelReactionJournal


def _unique_pairs(items):
    result = {}
    for key,value in items:
        if key in result:
            raise ValueError("duplicate JSON field: " + key)
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),object_pairs_hook=_unique_pairs)


def run(forecast_path, series_path, *, observation_at, journal_path=None):
    if journal_path is not None and Path(journal_path).resolve() in {Path(forecast_path).resolve(),Path(series_path).resolve()}:
        raise ValueError("reaction journal cannot replace an input artifact")
    forecast = read_json(forecast_path)
    payload = read_json(series_path)
    instrument = Instrument(**payload["instrument"])
    series = CandleSeries(instrument,payload["timeframe"],tuple(Candle(**item) for item in payload["candles"]),payload.get("source","MOEX_ISS"))
    if "count" in payload and payload["count"] != series.count:
        raise ValueError("source series count contradicts candles")
    report = build_level_reaction_report(forecast,series,observation_at=observation_at)
    if journal_path is not None:
        journal = LevelReactionJournal(journal_path)
        try:
            journal.observe(forecast,series,observation_at=observation_at)
        finally:
            journal.close()
    return report


def render_report(report):
    names = {f"{tf}_RANGE_{side}_20": f"{title}, {role} за 20 свечей" for tf,title in (("D1","День"),("H1","Час"),("M15","15 минут")) for side,role in (("LOW","нижняя граница"),("HIGH","верхняя граница"))}
    names.update({"PROFILE_VAL":"Нижняя граница области основного объёма", "PROFILE_POC":"Цена наибольшего объёма", "PROFILE_VAH":"Верхняя граница области основного объёма"})
    lines = ["# Реакция цены на ранее записанные уровни", "", "Свечей наблюдения: " + str(report["supplied_bar_count"]), "", "| Уровень | Цена | Свечей, чей диапазон содержит уровень | Смен стороны закрытия | Возвратов |", "|---|---:|---:|---:|---:|"]
    for item in report["levels"]:
        values = [item["price"],item["range_contact_bars"],item["close_side_changes"],item["returns_to_initial_close_side"]]
        cells = ["нет данных" if v is None else str(v) for v in values]
        label = names[item["level_name"]] + (" (приблизительно)" if item["source_status"] == "AVAILABLE_APPROXIMATE" else "")
        lines.append("| " + " | ".join([label,*cells]) + " |")
    lines.extend(["", "Возврат считается на сторону первого закрытия вне уровня после закрытия с другой стороны.", "Описаны только переданные завершённые свечи; полнота торгового календаря не подтверждена.", "Сила уровня, закрепление и ложный пробой этим расчётом не определяются. Исходный прогноз сохранён без изменений."])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Факты реакции цены на заранее записанные уровни")
    parser.add_argument("--forecast",required=True)
    parser.add_argument("--candles",required=True)
    parser.add_argument("--observation-at",required=True)
    parser.add_argument("--journal",help="Отдельный журнал наблюдений; исходный прогноз не меняется")
    parser.add_argument("--format",choices=("json","text"),default="json")
    args = parser.parse_args()
    report = run(args.forecast,args.candles,observation_at=args.observation_at,journal_path=args.journal)
    print(render_report(report) if args.format == "text" else json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))


if __name__ == "__main__":
    main()
