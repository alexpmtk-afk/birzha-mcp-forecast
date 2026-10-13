"""Single-day bounded H1/M15 source probe and opt-in standalone DuckDB capture.

Default plan mode does not contact MOEX or open any database. Probe makes
read-only requests; ingest requires a dedicated archive filename and explicit
acknowledgement. Repeat per trading date/market after source verification.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from birzha.application.history_policy import require_historical_intraday_scope
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.research_intraday_fetch import prepare_one_day

MARKETS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
ARCHIVE_NAME = "birzha_intraday_research_2025_2026.duckdb"
MOSCOW = ZoneInfo("Europe/Moscow")


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "probe", "ingest"), default="plan")
    parser.add_argument("--date", required=True,
                        help="one Moscow trade date YYYY-MM-DD, 2025 or 2026 only")
    parser.add_argument("--markets", nargs="+", choices=MARKETS, default=list(MARKETS))
    parser.add_argument("--archive", type=Path, help="separate dedicated DuckDB file")
    parser.add_argument("--ack-research-write", action="store_true")
    parser.add_argument("--retry-partial", action="store_true", help="re-fetch source days previously marked with gaps")
    args = parser.parse_args(argv)

    try:
        day = date.fromisoformat(args.date)
        if day.isoformat() != args.date:
            raise ValueError("strict ISO date required")
        require_historical_intraday_scope("H1", args.date, args.date)
        if args.mode != "plan" and day >= datetime.now(MOSCOW).date():
            raise ValueError("research source probe requires a completed past Moscow day")
        if len(args.markets) != len(set(args.markets)):
            raise ValueError("each market only once per run")
        if args.mode == "ingest":
            if not args.ack_research_write:
                raise ValueError("ingest requires --ack-research-write")
            if args.archive is None or args.archive.name != ARCHIVE_NAME:
                raise ValueError(f"ingest requires a separate archive named {ARCHIVE_NAME}")
            if not args.archive.parent.is_dir() or args.archive.is_symlink():
                raise ValueError("existing regular archive parent and no symlinks required")
        if args.mode != "ingest" and args.ack_research_write:
            raise ValueError("write acknowledgement is meaningful only with ingest")
        if args.mode != "ingest" and args.retry_partial:
            raise ValueError("--retry-partial only applies to isolated research ingest")
    except ValueError as exc:
        parser.error(str(exc))

    # Critical safety property: planning does not instantiate a network
    # provider or even a DuckDB connection.
    if args.mode == "plan":
        print(json.dumps({
            "status": "PLAN_ONLY_NO_NETWORK_NO_WRITE",
            "date": args.date, "markets": args.markets,
            "native": ["H1", "M1"], "persist": ["H1", "M15"],
            "raw_M1_persisted": False,
            "scope": "FIXED_2025_2026_RESEARCH",
        }, ensure_ascii=False, sort_keys=True))
        return 0

    market_data = MarketDataService.default()
    archive = ResearchIntradayArchive(str(args.archive)) if args.mode == "ingest" else None
    failed = False
    try:
        for symbol in args.markets:
            try:
                instrument = market_data.resolve(symbol, as_of=day)
                quality = archive.capture_quality(instrument, args.date) if archive is not None else None
                if quality is not None and not (quality == "CAPTURED_WITH_GAPS" and args.retry_partial):
                    print(json.dumps({
                        "market": symbol, "secid": instrument.secid, "date": args.date,
                        "status": "SKIPPED_ALREADY_CAPTURED", "source_quality": quality,
                        "note": "checkpoint records source receipt; not independent calendar/PIT verification",
                    }, sort_keys=True, ensure_ascii=False), flush=True)
                    continue
                capture = prepare_one_day(market_data, instrument, args.date)
                item = {
                    "market": symbol, "secid": instrument.secid,
                    "date": args.date,
                    "h1_rows": capture.hourly.count,
                    "m15_rows": capture.quarter_hour.count,
                    "raw_m1_rows_transient": capture.minute_rows_seen,
                    "incomplete_m15_buckets": capture.incomplete_m15_buckets,
                    "skipped_h1_bars": capture.skipped_h1_bars,
                    "availability_claim": "SOURCE_RECEIVED_NOT_PIT_VERIFIED",
                }
                if capture.hourly.count == 0 or capture.quarter_hour.count == 0:
                    item["status"] = "MISSING_INPUTS"
                    failed = True
                elif capture.incomplete_m15_buckets or capture.skipped_h1_bars:
                    item["status"] = "PARTIAL_NOT_FULLY_VERIFIED"
                    failed = True
                else:
                    item["status"] = "SOURCE_SAMPLE_PASS"
                if archive is not None:
                    item["stored"] = []
                    for series in (capture.hourly, capture.quarter_hour):
                        if series.candles:
                            item["stored"].append(archive.ingest(series))
                    if capture.hourly.count and capture.quarter_hour.count:
                        archive.mark_captured(
                            instrument, args.date,
                            h1_rows=capture.hourly.count,
                            m15_rows=capture.quarter_hour.count,
                            raw_m1_rows=capture.minute_rows_seen,
                            incomplete_m15_buckets=capture.incomplete_m15_buckets,
                            skipped_h1_bars=capture.skipped_h1_bars,
                        )
                print(json.dumps(item, sort_keys=True, ensure_ascii=False), flush=True)
            except Exception as exc:
                failed = True
                print(json.dumps({
                    "market": symbol, "date": args.date, "status": "ERROR",
                    "error": type(exc).__name__ + ": " + str(exc)[:300],
                }, ensure_ascii=False, sort_keys=True), flush=True)
    finally:
        if archive is not None:
            archive.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
