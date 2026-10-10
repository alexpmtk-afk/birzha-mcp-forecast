"""Consecutive close observations with external expected-bar evidence."""
import argparse
import json
from pathlib import Path
from birzha.application.level_holding import _unique_pairs

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),object_pairs_hook=_unique_pairs)
from birzha.application.level_holding import build_level_holding_report
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.level_holding import LevelHoldingJournal


def run(forecast_path,series_path,*,observation_at,schedule_path=None,journal_path=None):
    sources=[Path(p).resolve() for p in (forecast_path,series_path,schedule_path) if p is not None]
    if journal_path is not None and Path(journal_path).resolve() in sources:
        raise ValueError("holding journal cannot replace an input artifact")
    forecast=read_json(forecast_path); payload=read_json(series_path)
    series=CandleSeries(Instrument(**payload["instrument"]),payload["timeframe"],tuple(Candle(**c) for c in payload["candles"]),payload.get("source","MOEX_ISS"))
    if "count" in payload and payload["count"]!=series.count:raise ValueError("source count conflict")
    schedule_bytes=Path(schedule_path).read_bytes() if schedule_path is not None else None
    report=build_level_holding_report(forecast,series,observation_at=observation_at,schedule_bytes=schedule_bytes)
    if journal_path is not None:
        journal=LevelHoldingJournal(journal_path)
        try:journal.observe(forecast,series,observation_at=observation_at,schedule_bytes=schedule_bytes)
        finally:journal.close()
    return report


def render_report(report):
    lines=["# Последовательные закрытия относительно уровня","","Полнота окна: "+("свечи совпадают с переданным расписанием" if report["continuity"]["status"]=="MATCHES_DECLARED_SCHEDULE" else "не подтверждена"),"","| Источник уровня | Цена | Максимум закрытий выше | На уровне | Ниже |","|---|---:|---:|---:|---:|"]
    for i,item in enumerate(report["levels"]):
        names=["День: нижняя граница","День: верхняя граница","Час: нижняя граница","Час: верхняя граница","15 минут: нижняя граница","15 минут: верхняя граница","Профиль: нижняя граница","Профиль: наибольший объём","Профиль: верхняя граница"]
        counts=item["longest_close_runs"]
        values=[str(item["price"]) if item["price"] is not None else "нет данных"]+[str(counts[s]) if counts is not None else "нет данных" for s in ("ABOVE","ON","BELOW")]
        lines.append("| "+" | ".join([names[i],*values])+" |")
    lines.extend(["","Пропуски не сжимаются в непрерывную серию. Сила удержания не назначена.","Время между закрытиями не означает непрерывное пребывание цены над уровнем.","Подлинность внешнего расписания требует отдельной проверки; прогноз не меняется."])
    return "\n".join(lines)


def main():
    parser=argparse.ArgumentParser(description="Последовательные закрытия и проверка пропусков")
    parser.add_argument("--forecast",required=True);parser.add_argument("--candles",required=True)
    parser.add_argument("--observation-at",required=True);parser.add_argument("--schedule")
    parser.add_argument("--journal");parser.add_argument("--format",choices=("json","text"),default="json")
    args=parser.parse_args()
    report=run(args.forecast,args.candles,observation_at=args.observation_at,schedule_path=args.schedule,journal_path=args.journal)
    print(render_report(report) if args.format=="text" else json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2))


if __name__=="__main__":main()
