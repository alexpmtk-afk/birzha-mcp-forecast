"""SBER-only bounded, resumable 2025-26 research backfill.

Every Moscow CIVIL date is considered: 2025/26 also contain weekend sessions.
The official exchange trade_date/session assignment is not inferred from the
weekday or the candle date. Until independently reconciled, gap days remain
problems/unknowns, never silently certified exchange holidays.

No production or D1 writes. No raw M1 persistence. Default is offline PLAN.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import time

import duckdb

from birzha.application.history_policy import (
    latest_safe_d1_calendar_date, require_historical_intraday_scope,
)
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.research_intraday_fetch import prepare_one_day
from scripts.g2_intraday_research_archive import ARCHIVE_NAME

FIRST = date(2025, 1, 1)
LAST = date(2026, 12, 31)
SCHEMA = """
CREATE TABLE IF NOT EXISTS research_sber_batch_dates (
    civil_date VARCHAR PRIMARY KEY,
    secid VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    h1_rows INTEGER NOT NULL,
    m15_rows INTEGER NOT NULL,
    raw_m1_rows INTEGER NOT NULL,
    incomplete_m15_buckets INTEGER NOT NULL,
    skipped_h1_bars INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    detail VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL
)
"""
UNRESOLVED = frozenset({
    "EMPTY_SOURCE_CALENDAR_UNVERIFIED", "MISSING_INPUTS",
    "CAPTURED_WITH_GAPS", "ERROR",
})


def days_between(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def emit(**data) -> None:
    print(json.dumps(data, ensure_ascii=False, sort_keys=True), flush=True)


def validate_archive_path(path: Path) -> None:
    # An explicitly designated SBER research directory is required, not a
    # generic HOME/production database. Never create directories ourselves.
    if (path.name != ARCHIVE_NAME or path.parent.name != "SBER" or
            path.parent.parent.name != "research" or
            not path.parent.is_dir() or path.is_symlink() or
            path.parent.is_symlink() or path.parent.parent.is_symlink() or
            (path.exists() and not path.is_file())):
        raise ValueError("only an existing research/SBER directory and dedicated archive filename are accepted")


def record(db, day: str, status: str, counts: dict, detail: str) -> None:
    if len(detail) > 200000:
        raise ValueError("audit detail unexpectedly large; no truncated gap report allowed")
    previous = db.execute(
        "SELECT attempts FROM research_sber_batch_dates WHERE civil_date=?", [day]
    ).fetchone()
    count = int(previous[0]) + 1 if previous else 1
    db.execute(
        """INSERT INTO research_sber_batch_dates
           VALUES (?, 'SBER', ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (civil_date) DO UPDATE SET
           secid=excluded.secid, status=excluded.status,
           h1_rows=excluded.h1_rows, m15_rows=excluded.m15_rows,
           raw_m1_rows=excluded.raw_m1_rows,
           incomplete_m15_buckets=excluded.incomplete_m15_buckets,
           skipped_h1_bars=excluded.skipped_h1_bars,
           attempts=excluded.attempts, detail=excluded.detail,
           updated_at=excluded.updated_at""",
        [day, status, counts.get("H1", 0), counts.get("M15", 0),
         counts.get("M1", 0), counts.get("gaps", 0),
         counts.get("short_h1", 0), count, detail,
         datetime.now(timezone.utc).isoformat()],
    )


def ensure_research_marker(conn) -> None:
    found = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name='research_intraday_meta'"
    ).fetchone()[0]
    if not found or conn.execute(
        "SELECT value FROM research_intraday_meta WHERE key='archive_id'"
    ).fetchone() != ("BIRZHA_RESEARCH_INTRADAY_2025_2026_V1",):
        raise ValueError("archive research marker missing or incorrect")


def ensure_sber_only(conn) -> None:
    """Reject mixed-market research databases BEFORE a write connection opens."""
    ensure_research_marker(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables"
    ).fetchall()}
    if "research_intraday_candles" in tables:
        mixed = conn.execute(
            """SELECT count(*) FROM research_intraday_candles
               WHERE symbol!='SBER' OR secid!='SBER'
                  OR engine!='stock' OR market!='shares' OR board!='TQBR'"""
        ).fetchone()[0]
        if mixed:
            raise ValueError("SBER research path contains other-market or non-TQBR candles")
    if "research_intraday_captured_days" in tables:
        mixed = conn.execute(
            """SELECT count(*) FROM research_intraday_captured_days
               WHERE symbol!='SBER' OR secid!='SBER'"""
        ).fetchone()[0]
        if mixed:
            raise ValueError("SBER research path contains non-SBER capture checkpoints")
    if "research_sber_batch_dates" in tables:
        mixed = conn.execute(
            """SELECT count(*) FROM research_sber_batch_dates WHERE secid!='SBER'"""
        ).fetchone()[0]
        if mixed:
            raise ValueError("SBER research path contains non-SBER journal entries")


def report(path: Path, start: date, end: date) -> dict:
    validate_archive_path(path)
    if not path.exists():
        raise ValueError("no existing SBER archive for reporting")
    conn = duckdb.connect(str(path), read_only=True)
    try:
        ensure_sber_only(conn)
        journal_exists = conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='research_sber_batch_dates'"
        ).fetchone()[0]
        journal = {}
        if journal_exists:
            journal = {r[0]: r for r in conn.execute(
                """SELECT civil_date, status, h1_rows, m15_rows,
                          incomplete_m15_buckets, skipped_h1_bars,
                          attempts, detail FROM research_sber_batch_dates
                   WHERE civil_date BETWEEN ? AND ?""",
                [start.isoformat(), end.isoformat()],
            ).fetchall()}
        captured = {r[0]: r for r in conn.execute(
            """SELECT trade_date, quality FROM research_intraday_captured_days
               WHERE symbol='SBER' AND secid='SBER'
                 AND trade_date BETWEEN ? AND ?""",
            [start.isoformat(), end.isoformat()],
        ).fetchall()}
        counts = dict(conn.execute(
            """SELECT timeframe, count(*) FROM research_intraday_candles
               WHERE symbol='SBER' AND secid='SBER'
                 AND trade_date BETWEEN ? AND ? GROUP BY timeframe""",
            [start.isoformat(), end.isoformat()],
        ).fetchall())
        duplicates = conn.execute(
            """SELECT count(*) FROM (
                 SELECT secid,timeframe,begin, count(*) AS n
                 FROM research_intraday_candles
                 WHERE symbol='SBER' GROUP BY 1,2,3 HAVING count(*)>1
               )"""
        ).fetchone()[0]
        statuses: dict[str, int] = {}
        outstanding = []
        for d in days_between(start, end):
            key = d.isoformat()
            entry = journal.get(key)
            status = entry[1] if entry else (captured[key][1] if key in captured else "UNVISITED")
            statuses[status] = statuses.get(status, 0) + 1
            if status != "CAPTURED_SOURCE_RESPONSE":
                outstanding.append({
                    "date": key, "status": status,
                    "h1": entry[2] if entry else 0,
                    "m15": entry[3] if entry else 0,
                    "incomplete_m15": entry[4] if entry else 0,
                    "invalid_h1": entry[5] if entry else 0,
                    "attempts": entry[6] if entry else 0,
                    "detail": entry[7] if entry else "calendar / source unverified",
                })
        return {
            "status": "COVERAGE_UNCERTIFIED_NO_INDEPENDENT_MOEX_SESSION_CALENDAR",
            "from": start.isoformat(), "through": end.isoformat(),
            "civil_days": (end-start).days+1,
            "counts_by_status": statuses,
            "native_h1_stored": int(counts.get("H1", 0)),
            "strict_m15_stored": int(counts.get("M15", 0)),
            "duplicates": int(duplicates),
            "archive_bytes": path.stat().st_size,
            "unresolved_dates": outstanding,
            "session_trade_date_mapping": "UNVERIFIED",
            "pit_first_receipt": "UNVERIFIED",
        }
    finally:
        conn.close()


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "probe", "ingest", "report"), default="plan")
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--through-date", required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--max-days", type=int, default=3,
                        help="maximum newly attempted civil days in this invocation")
    parser.add_argument("--chunk-days", type=int, default=2,
                        help="pause between groups of newly attempted days (1..5)")
    parser.add_argument("--pause-seconds", type=float, default=1.0)
    parser.add_argument("--ack-research-write", action="store_true")
    parser.add_argument("--ack-source-verified", action="store_true",
                        help="explicit gate for source pilot after offline tests")
    parser.add_argument("--ack-reviewed-pilot", action="store_true",
                        help="required to process over five dates in one run")
    parser.add_argument("--retry-partial", action="store_true")
    parser.add_argument("--retry-missing", action="store_true")
    args = parser.parse_args(argv)
    try:
        start, end = date.fromisoformat(args.from_date), date.fromisoformat(args.through_date)
        if start.isoformat() != args.from_date or end.isoformat() != args.through_date:
            raise ValueError("exact ISO date YYYY-MM-DD required")
        if not FIRST <= start <= end <= LAST:
            raise ValueError("SBER backfill restricted to 2025-01-01..2026-12-31")
        require_historical_intraday_scope("H1", args.from_date, args.through_date)
        if args.mode in ("probe", "ingest") and end.isoformat() > latest_safe_d1_calendar_date():
            raise ValueError("source end date beyond conservative published-session cutoff")
        if not 1 <= args.chunk_days <= 5 or not 0 <= args.pause_seconds <= 15:
            raise ValueError("chunk-days must be 1..5 and pause-seconds 0..15")
        if not 1 <= args.max_days <= (732 if args.ack_reviewed_pilot else 5):
            raise ValueError("max-days must be 1..5 without reviewed-pilot gate")
        if args.mode == "ingest":
            if not args.ack_research_write or not args.ack_source_verified:
                raise ValueError("ingest requires both research-write and source-verified acknowledgements")
            if args.archive is None:
                raise ValueError("ingest requires dedicated --archive path")
            validate_archive_path(args.archive)
            # Fail closed on a marked but mixed-market archive without even
            # opening a writable DuckDB connection.
            if args.archive.exists():
                inspection = duckdb.connect(str(args.archive), read_only=True)
                try:
                    ensure_sber_only(inspection)
                finally:
                    inspection.close()
        if args.mode == "report":
            if args.archive is None:
                raise ValueError("report needs dedicated --archive")
            validate_archive_path(args.archive)
        if args.mode in ("probe", "plan") and args.archive is not None:
            raise ValueError("probe/plan must not access any archive")
        if args.mode != "ingest" and (args.ack_research_write or args.ack_source_verified or
                                      args.retry_missing or args.retry_partial):
            raise ValueError("write/retry flags only apply to ingest")
    except ValueError as exc:
        parser.error(str(exc))

    if args.mode == "plan":
        emit(status="PLAN_ONLY_NO_NETWORK_NO_DB", symbol="SBER",
             from_date=args.from_date, through_date=args.through_date,
             calendar_days=(end-start).days+1,
             note="Includes weekends: exchange session validation still required",
             network_requests="ZERO", raw_m1_persisted=False,
             max_days=args.max_days, chunk_days=args.chunk_days)
        return 0
    if args.mode == "report":
        emit(**report(args.archive, start, end))
        return 0

    archive = ResearchIntradayArchive(str(args.archive)) if args.mode == "ingest" else None
    provider = None
    tried, failures = 0, 0
    try:
        if archive:
            archive.conn.execute(SCHEMA)
        for d in days_between(start, end):
            if tried >= args.max_days:
                break
            key = d.isoformat()
            if archive:
                past = archive.conn.execute(
                    "SELECT status FROM research_sber_batch_dates WHERE civil_date=?",
                    [key],
                ).fetchone()
                past_status = past[0] if past else None
                if past_status in ("EMPTY_SOURCE_CALENDAR_UNVERIFIED", "MISSING_INPUTS") and not args.retry_missing:
                    emit(date=key, status="SKIPPED_KNOWN_UNRESOLVED", prior_status=past_status)
                    continue
                if past_status == "CAPTURED_SOURCE_RESPONSE":
                    emit(date=key, status="SKIPPED_ALREADY_CAPTURED_NO_NETWORK")
                    continue
                if past_status == "CAPTURED_WITH_GAPS" and not args.retry_partial:
                    emit(date=key, status="SKIPPED_PARTIAL_NOT_CERTIFIED")
                    continue
            try:
                if provider is None:
                    provider = MarketDataService.default()
                identity = provider.resolve("SBER", as_of=d)
                if (identity.symbol, identity.secid, identity.engine,
                        identity.market, identity.board) != (
                        "SBER", "SBER", "stock", "shares", "TQBR"):
                    raise ValueError("historical SBER/TQBR instrument identity mismatch")
                if archive:
                    prior_quality = archive.capture_quality(identity, key)
                    if prior_quality and not (
                            prior_quality == "CAPTURED_WITH_GAPS" and args.retry_partial):
                        emit(date=key, status="SKIPPED_CHECKPOINT", quality=prior_quality)
                        continue
                capture = prepare_one_day(provider, identity, key)
                counts = {"H1": capture.hourly.count, "M15": capture.quarter_hour.count,
                          "M1": capture.minute_rows_seen, "gaps": capture.incomplete_m15_buckets,
                          "short_h1": capture.skipped_h1_bars}
                if not counts["H1"] and not counts["M15"]:
                    status = "EMPTY_SOURCE_CALENDAR_UNVERIFIED"
                elif not counts["H1"] or not counts["M15"]:
                    status = "MISSING_INPUTS"
                elif counts["gaps"] or counts["short_h1"]:
                    status = "CAPTURED_WITH_GAPS"
                else:
                    status = "CAPTURED_SOURCE_RESPONSE"
                if archive:
                    for series in (capture.hourly, capture.quarter_hour):
                        if series.candles:
                            archive.ingest(series)
                    if counts["H1"] and counts["M15"]:
                        archive.mark_captured(
                            identity, key, h1_rows=counts["H1"],
                            m15_rows=counts["M15"], raw_m1_rows=counts["M1"],
                            incomplete_m15_buckets=counts["gaps"],
                            skipped_h1_bars=counts["short_h1"],
                        )
                    record(archive.conn, key, status, counts,
                           json.dumps({"trade_session": "UNVERIFIED",
                                       "m15_gap_events": capture.m15_gap_events,
                                       "h1_gap_events": capture.h1_gap_events},
                                      sort_keys=True, ensure_ascii=False))
                if status != "CAPTURED_SOURCE_RESPONSE":
                    failures += 1
                emit(date=key, status=status, **counts,
                     raw_m1_persisted=False, trade_session_verified=False)
            except Exception as exc:
                failures += 1
                if archive:
                    record(archive.conn, key, "ERROR", {},
                           type(exc).__name__ + ": " + str(exc)[:500])
                emit(date=key, status="ERROR", error=type(exc).__name__ + ": " + str(exc)[:500])
            tried += 1
            if args.pause_seconds and tried % args.chunk_days == 0 and tried < args.max_days:
                time.sleep(args.pause_seconds)
    finally:
        if archive:
            archive.close()
    emit(status="BATCH_DONE_SOURCE_NOT_CALENDAR_CERTIFIED",
         newly_attempted=tried, flagged_dates=failures,
         resume="rerun identical command; known source days are skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
