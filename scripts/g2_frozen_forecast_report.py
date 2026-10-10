"""Render immutable six-market Forecast Record source evidence, read-only."""
from __future__ import annotations
import argparse
from pathlib import Path
from zipfile import ZipFile
from scripts.g2_post_maturity_outcome_readiness import verify_frozen, readiness

def display(value):
    return "UNAVAILABLE" if value is None else str(value)

def generate_report(frozen_zip: Path) -> str:
    with ZipFile(frozen_zip) as archive:
        records=verify_frozen(archive)
    report=readiness(frozen_zip)
    if report["forecast_count"]!=6 or report["horizon_count"]!=18:
        raise ValueError("six original forecasts and 18 horizons required")
    if report["canonical_outcomes_appended"]!=0:
        raise ValueError("not the frozen initial snapshot")
    lines=[
        "# Биржа — зафиксированные прогнозы на реальных исходных данных",
        "",
        "**Режим:** staging / UNVALIDATED. Ни одна метрика будущего качества не установлена.",
        "**Данные:** оригинальные MOEX-ответы с подтвержденными SHA, без независимой подписи времени.",
        "",
        "| Рынок | SECID | P0 | Направление | Выпущен в UTC |",
        "|---|---|---:|---|---|",
    ]
    for r in records:
        lines.append(f'| {r["market"]} | {r["secid"]} | {display(r["reference_price"])} | {r["direction"]} | {r["t0"]} |')
    for r in records:
        lines.extend([
            "", "## "+r["market"]+" — "+r["secid"], "",
            "- Forecast ID: "+r["forecast_id"],
            "- Snapshot ID: "+r["snapshot_id"],
            "- Оригинальный Forecast Record SHA: "+r["receipt_sha256"],
            "- Исходный payload bundle SHA: "+r["source_sha256"],
            "",
            "| Горизонт | Направление | Expected move, % (сценарная оценка) | Adverse move, % | Outcome |",
            "|---|---|---:|---:|---|",
        ])
        for h in sorted(r["horizons"],key=lambda x:x["sessions"]):
            lines.append(f'| {h["sessions"]} сессий | {h["direction"]} | {display(h.get("expected_move_pct"))} | {display(h.get("adverse_move_pct"))} | PENDING |')
    lines.extend([
        "", "---", "", "## Ограничения",
        "",
        "- Все 18 горизонтов PENDING: нельзя рассчитать точность заранее.",
        "- Настоящая first-touch оценка требует будущих M15/M1/trades, не только цены в конце горизонта.",
        "- Отсутствующие Control/Route/Scenario не достраиваются искусственно.",
        "- Документ не меняет Forecast Record, журнал или модель и не является торговой рекомендацией.",
        "- В HOME / production эти тестовые прогнозы не развёрнуты.",
        "",
        "Оригинальный архив SHA-256: "+report["source_frozen_zip_sha256"],""
    ])
    return "\n".join(lines)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--frozen-zip",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    if args.output.exists():
        raise ValueError("refusing forecast report overwrite")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    text=generate_report(args.frozen_zip)
    args.output.write_text(text,encoding="utf8")
    print("frozen_report="+str(args.output)+" bytes="+str(len(text.encode("utf8"))))

if __name__=="__main__":
    main()
