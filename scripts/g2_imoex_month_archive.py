"""One-month isolated IMOEX H1/M15 research capture with explicit local DuckDB output.

No production DB, no Google Sheets. Historical M1 is only held in RAM,
converted into strictly complete M15 bars, then discarded. No PIT claim.
The CLI is deliberately a single-month bounded operation, not a bulk
two-year collector. Subsequent months require their own authorization/run.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from birzha.application.history_policy import require_historical_intraday_scope, latest_safe_d1_calendar_date
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.research_intraday_fetch import prepare_one_day

MOSCOW = ZoneInfo("Europe/Moscow")
APPROVED_MARKET = "IMOEX"
ARCHIVE_NAME = "birzha_intraday_research_2025_2026.duckdb"


def month_bounds(year: int, month: int) -> tuple[str, str]:
    if year not in (2025, 2026) or not 1 <= month <= 12:
        raise ValueError("research month restricted to 2025-2026")
    left = date(year, month, 1)
    right = (date(year + (month == 12), month % 12 + 1, 1)
             - timedelta(days=1))
    require_historical_intraday_scope("H1", left.isoformat(), right.isoformat())
    return left.isoformat(), right.isoformat()


def _write_report(path: Path, report: dict) -> str:
    if path.exists() or path.is_symlink():
        raise ValueError("refusing to overwrite existing research report")
    if not path.parent.is_dir() or path.suffix != ".json":
        raise ValueError("report destination must be a new .json in existing folder")
    raw = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False,
                      allow_nan=False) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--mode", choices=("plan", "ingest"), default="plan")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--ack-research-write", action="store_true")
    parser.add_argument("--max-source-days", type=int, default=31)
    parser.add_argument("--till-date", help="explicit safe published date within a not-yet-finished month")
    args = parser.parse_args(argv)
    try:
        left, month_end = month_bounds(args.year, args.month)
        right = args.till_date or month_end
        if args.till_date is not None:
            if date.fromisoformat(right).isoformat() != right or not left <= right <= month_end:
                raise ValueError("partial-month --till-date must be ISO within requested month")
            if right > latest_safe_d1_calendar_date():
                raise ValueError("partial month cannot include unpublished D1 session")
        if not 1 <= args.max_source_days <= 31:
            raise ValueError("max-source-days must be 1..31")
        today = datetime.now(MOSCOW).date().isoformat()
        if args.mode == "ingest":
            if not args.ack_research_write:
                raise ValueError("explicit --ack-research-write required")
            if args.archive is None or args.archive.name != ARCHIVE_NAME:
                raise ValueError("archive must be isolated research file with approved name")
            if args.report is None or args.report.suffix != ".json":
                raise ValueError("explicit new JSON audit report required")
            if args.archive.is_symlink() or args.report.is_symlink():
                raise ValueError("no symlink inputs or outputs")
            if not args.archive.parent.is_dir() or not args.report.parent.is_dir():
                raise ValueError("parent folder must exist before run")
            if args.report.exists():
                raise ValueError("refusing to overwrite historical monthly report")
            if left >= today or right >= today:
                raise ValueError("research interval must have fully concluded in Moscow")
            if args.till_date is None and month_end >= today:
                raise ValueError("current month requires explicit --till-date for published D1")
        elif args.ack_research_write or args.archive is not None or args.report is not None:
            raise ValueError("plan cannot accept archive/write arguments")
    except ValueError as exc:
        parser.error(str(exc))

    if args.mode == "plan":
        print(json.dumps({"status": "PLAN_ONLY_NO_NETWORK_NO_WRITE",
                          "market": APPROVED_MARKET, "from_date": left,
                          "till_date": right, "H1_source": "MOEX_NATIVE_H1",
                          "M15_source": "MOEX_M1_15_OF_15",
                          "raw_M1_persisted": False},
                         sort_keys=True))
        return 0

    market_data = MarketDataService.default()
    instrument = market_data.resolve(APPROVED_MARKET, as_of=date.fromisoformat(right))
    if instrument.symbol != APPROVED_MARKET or instrument.secid != APPROVED_MARKET or instrument.asset_class != "index":
        raise ValueError("exact IMOEX index SECID required, no symbol substitution")

    # Candidate dates come from exchange-provided completed D1 candles,
    # NOT a fabricated Mon-Fri calendar. The D1/activity discrepancy for
    # four 2026 weekends must remain visible, not silently trimmed.
    d1 = market_data.candles_for_instrument(
        instrument, timeframe="D1", from_date=left,
        till_date=right, completed_only=True,
    )
    candidates = sorted({
        c.begin[:10] for c in d1.candles if
        c.completed and isinstance(c.close, (int, float)) and c.close > 0
        and left <= c.begin[:10] <= right
    })
    if not candidates:
        raise ValueError("no native D1 candidate source days; no complete archive claimed")
    if len(candidates) > args.max_source_days:
        raise ValueError("requested calendar exceeds bounded max-source-days")
    archive = ResearchIntradayArchive(str(args.archive))
    rows = []
    errors = 0
    try:
        for day in candidates:
            try:
                quality = archive.capture_quality(instrument, day)
                if quality is not None:
                    rows.append({"date": day, "secid": instrument.secid,
                                 "state": "ALREADY_CAPTURED",
                                 "quality": quality})
                    continue
                source = prepare_one_day(market_data, instrument, day)
                if source.hourly.count:
                    archive.ingest(source.hourly)
                if source.quarter_hour.count:
                    archive.ingest(source.quarter_hour)
                if source.hourly.count and source.quarter_hour.count:
                    archive.mark_captured(
                        instrument, day,
                        h1_rows=source.hourly.count,
                        m15_rows=source.quarter_hour.count,
                        raw_m1_rows=source.minute_rows_seen,
                        incomplete_m15_buckets=source.incomplete_m15_buckets,
                        skipped_h1_bars=source.skipped_h1_bars,
                    )
                quality = archive.capture_quality(instrument, day)
                if quality is None:
                    errors += 1
                rows.append({
                    "date": day, "secid": instrument.secid,
                    "state": quality or "MISSING_INPUTS",
                    "h1_rows": source.hourly.count,
                    "m15_rows": source.quarter_hour.count,
                    "raw_m1_transient": source.minute_rows_seen,
                    "partial_m15_buckets": source.incomplete_m15_buckets,
                    "short_h1_bars": source.skipped_h1_bars,
                })
            except Exception as exc:
                errors += 1
                rows.append({"date": day, "state": "SOURCE_ERROR",
                             "error_type": type(exc).__name__,
                             "error": str(exc)[:250]})
            print(json.dumps(rows[-1], sort_keys=True, ensure_ascii=False), flush=True)
        inventory = archive.inventory()
        captured = sum(x["state"] in (
            "CAPTURED_SOURCE_RESPONSE", "CAPTURED_WITH_GAPS", "ALREADY_CAPTURED"
        ) for x in rows)
        report = {
            "schema": "G2_IMOEX_MONTHLY_ARCHIVE_V1",
            "symbol": "IMOEX", "secid": instrument.secid,
            "from_date": left, "till_date": right,
            "d1_source_candidate_days": len(candidates),
            "days_with_captured_price_rows": captured,
            "days_missing_source": errors,
            "days_with_gaps": sum(
                x.get("quality", x["state"]) == "CAPTURED_WITH_GAPS" for x in rows
            ),
            "strict_m15_15_of_15_only": True,
            "minute_m1_persisted": False,
            "historical_first_receipt": "NOT_PROVEN",
            "independent_calendar_verified": False,
            "full_forecast_v1_parity_verified": False,
            "archive_is_not_production": True,
            "inventory": inventory,
            "days": rows,
        }
        digest = _write_report(args.report, report)
        print(json.dumps({"status": ("SOURCE_CAPTURED_WITH_WARNINGS" if errors == 0
                                     else "PARTIAL_MISSING_SOURCE"),
                          "d1_candidates": len(candidates), "captured_days": captured,
                          "missing_days": errors,
                          "inventory": inventory,
                          "report_sha256": digest}, sort_keys=True), flush=True)
        # Expected source gaps are explicit warnings, but a missing whole day
        # must fail; upload-artifact must still be invoked with if: always().
        return 0 if errors == 0 else 1
    finally:
        archive.close()


if __name__ == "__main__":
    raise SystemExit(run())
