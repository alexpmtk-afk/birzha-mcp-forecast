"""Bounded, resumable Si-only MOEX H1/M15 historical research capture.

Expected sessions come from the exact-contract MOEX ISS history calendar. The
existing D1 archive is opened read-only and is used only to discover contracts
and report whether the matching daily candle is available. One batch handles a
small number of sessions, saves a per-session receipt, then advances its cursor.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

import duckdb

from birzha.application.history_policy import require_historical_intraday_scope
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.research_intraday_fetch import prepare_one_day
from birzha.providers.moex_calendar import MoexTradingCalendar

_MOSCOW = ZoneInfo("Europe/Moscow")
_ARCHIVE_NAME = "birzha_intraday_research_2025_2026.duckdb"
_CALENDAR_SCHEMA = "BIRZHA_SI_MOEX_CALENDAR_V1"
_PROGRESS_SCHEMA = "BIRZHA_SI_INTRADAY_PROGRESS_V1"
_LEDGER_SCHEMA = "BIRZHA_SI_INTRADAY_COVERAGE_V1"
_MAX_BATCH_SESSIONS = 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_source(path: Path, first: str, last: str) -> tuple[list[dict[str, str]], set[tuple[str, str]]]:
    if path.is_symlink() or not path.is_file() or path.suffix != ".duckdb":
        raise ValueError("existing regular D1 source DuckDB file required")
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if "historical_candles" not in tables:
            raise ValueError("D1 source is missing historical_candles")
        columns = {row[0] for row in con.execute("DESCRIBE historical_candles").fetchall()}
        required = {"symbol", "secid", "engine", "market", "board", "timeframe", "begin", "completed"}
        if not required.issubset(columns):
            raise ValueError("D1 source schema is not the expected historical archive")
        contracts = con.execute("""
            SELECT DISTINCT secid, engine, market, board
            FROM historical_candles
            WHERE symbol='Si' AND timeframe='D1'
              AND substr(begin,1,10) BETWEEN ? AND ?
            ORDER BY secid
        """, [first, last]).fetchall()
        if not contracts:
            raise ValueError("D1 source has no Si contracts in the requested range")
        daily = con.execute("""
            SELECT DISTINCT secid, substr(begin,1,10)
            FROM historical_candles
            WHERE symbol='Si' AND timeframe='D1' AND completed IS TRUE
              AND substr(begin,1,10) BETWEEN ? AND ?
        """, [first, last]).fetchall()
        return ([{"secid": str(s), "engine": str(e), "market": str(m), "board": str(b)}
                 for s, e, m, b in contracts], {(str(s), str(d)) for s, d in daily})
    finally:
        con.close()


def _atomic_json(path: Path, value: dict[str, object]) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing symlink output: {path.name}")
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _load_calendar(path: Path, *, first: str, last: str, d1_sha: str,
                   market_data: MarketDataService,
                   contracts: list[dict[str, str]]) -> dict[str, object]:
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError("calendar manifest must be a regular file")
        cached = json.loads(path.read_text(encoding="utf-8"))
        if (cached.get("schema") != _CALENDAR_SCHEMA or cached.get("from_date") != first
                or cached.get("till_date") != last or cached.get("d1_archive_sha256") != d1_sha):
            raise ValueError("calendar manifest belongs to different source or date range")
        return cached

    # The final session can use a contract issued after the last D1 archive row.
    # Resolve the end date explicitly so its exact contract enters the calendar.
    end_contract = market_data.resolve("Si", as_of=date.fromisoformat(last))
    contract_map = {item["secid"]: item for item in contracts}
    contract_map[end_contract.secid] = {
        "secid": end_contract.secid, "engine": end_contract.engine,
        "market": end_contract.market, "board": end_contract.board,
    }
    calendar = MoexTradingCalendar(market_data.provider)
    sessions: dict[str, list[str]] = {}
    for secid, identity in sorted(contract_map.items()):
        dates = calendar.dates(
            engine=identity["engine"], market=identity["market"], board=identity["board"],
            security=secid, from_date=date.fromisoformat(first), till_date=date.fromisoformat(last),
        )
        sessions[secid] = [item.isoformat() for item in dates]
    body: dict[str, object] = {
        "schema": _CALENDAR_SCHEMA,
        "symbol": "Si",
        "from_date": first,
        "till_date": last,
        "d1_archive_sha256": d1_sha,
        "source": "MOEX_ISS_EXACT_CONTRACT_HISTORY_CALENDAR",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "contracts": contract_map,
        "sessions_by_secid": sessions,
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body["normalized_calendar_sha256"] = hashlib.sha256(encoded).hexdigest()
    _atomic_json(path, body)
    return body


def _append_ledger(path: Path, record: dict[str, object]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("coverage ledger must be a regular file")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_progress(path: Path, *, first: str, last: str, d1_sha: str,
                   archive: Path, calendar_sha: str) -> int:
    if not path.exists():
        return 0
    if path.is_symlink() or not path.is_file():
        raise ValueError("progress record must be a regular file")
    state = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema": _PROGRESS_SCHEMA, "from_date": first, "till_date": last,
        "d1_archive_sha256": d1_sha, "research_archive": str(archive.resolve()),
        "normalized_calendar_sha256": calendar_sha,
    }
    if any(state.get(key) != value for key, value in expected.items()):
        raise ValueError("progress record does not match the selected archive, source or calendar")
    next_index = state.get("next_index")
    if type(next_index) is not int or next_index < 0:
        raise ValueError("invalid progress cursor")
    return next_index


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "ingest"), default="plan")
    parser.add_argument("--from-date", required=True)
    parser.add_argument("--till-date", required=True, help="last fully completed Moscow session date")
    parser.add_argument("--d1-archive", required=True, type=Path)
    parser.add_argument("--archive", type=Path, help="separate Si H1/M15 research DuckDB")
    parser.add_argument("--max-sessions", type=int, default=10,
                        help=f"small resumable batch, 1..{_MAX_BATCH_SESSIONS}")
    parser.add_argument("--ack-research-write", action="store_true")
    args = parser.parse_args(argv)

    try:
        first = date.fromisoformat(args.from_date).isoformat()
        last = date.fromisoformat(args.till_date).isoformat()
        if first != args.from_date or last != args.till_date or first > last:
            raise ValueError("strict ordered ISO dates required")
        require_historical_intraday_scope("H1", first, last)
        if date.fromisoformat(last) >= datetime.now(_MOSCOW).date():
            raise ValueError("till-date must be an already completed past Moscow date")
        if type(args.max_sessions) is not int or not 1 <= args.max_sessions <= _MAX_BATCH_SESSIONS:
            raise ValueError(f"max-sessions must be between 1 and {_MAX_BATCH_SESSIONS}")
        d1_sha = _sha256(args.d1_archive)
        contracts, d1_days = _read_source(args.d1_archive, first, last)
        if args.mode == "ingest":
            if not args.ack_research_write:
                raise ValueError("ingest requires --ack-research-write")
            if args.archive is None or args.archive.name != _ARCHIVE_NAME:
                raise ValueError(f"ingest requires a separate archive named {_ARCHIVE_NAME}")
            if not args.archive.parent.is_dir() or args.archive.is_symlink():
                raise ValueError("existing dedicated archive directory and no symlinks required")
        elif args.ack_research_write or args.archive is not None:
            raise ValueError("archive and write acknowledgement apply only to ingest")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    d1_session_dates = sorted(day for _, day in d1_days)
    if args.mode == "plan":
        print(json.dumps({
            "status": "PLAN_ONLY_NO_NETWORK_NO_WRITE",
            "symbol": "Si", "from_date": first, "till_date": last,
            "d1_archive_sessions": len(set(d1_session_dates)),
            "d1_archive_first": min(d1_session_dates) if d1_session_dates else None,
            "d1_archive_last": max(d1_session_dates) if d1_session_dates else None,
            "known_contracts": [item["secid"] for item in contracts],
            "intraday": ["H1", "M15"], "minute_source_persisted": False,
            "calendar_checked": False,
        }, ensure_ascii=False, sort_keys=True))
        return 0

    assert args.archive is not None
    calendar_path = args.archive.with_name(args.archive.stem + ".si-calendar.json")
    progress_path = args.archive.with_name(args.archive.stem + ".si-progress.json")
    ledger_path = args.archive.with_name(args.archive.stem + ".si-coverage.jsonl")
    market_data = MarketDataService.default()
    try:
        calendar_manifest = _load_calendar(
            calendar_path, first=first, last=last, d1_sha=d1_sha,
            market_data=market_data, contracts=contracts,
        )
    except Exception as exc:
        # Do not create the research archive or advance its cursor when the
        # authoritative session calendar cannot be checked.
        print(json.dumps({
            "status": "CALENDAR_UNAVAILABLE",
            "symbol": "Si",
            "from_date": first,
            "till_date": last,
            "detail": type(exc).__name__ + ": " + str(exc)[:300],
            "research_rows_written": 0,
            "progress_advanced": False,
        }, ensure_ascii=False, sort_keys=True))
        return 1
    calendar_sha = str(calendar_manifest["normalized_calendar_sha256"])
    by_contract = calendar_manifest["sessions_by_secid"]
    sessions = sorted({
        day for dates in by_contract.values() for day in dates
        if first <= day <= last
    })
    cursor = _load_progress(
        progress_path, first=first, last=last, d1_sha=d1_sha,
        archive=args.archive, calendar_sha=calendar_sha,
    )
    if cursor > len(sessions):
        parser.error("progress cursor exceeds the official session calendar")

    stop = min(cursor + args.max_sessions, len(sessions))
    store = ResearchIntradayArchive(str(args.archive))
    status_counts: dict[str, int] = {}
    failures = 0
    try:
        for index in range(cursor, stop):
            trade_date = sessions[index]
            record: dict[str, object] = {
                "schema": _LEDGER_SCHEMA, "symbol": "Si", "trade_date": trade_date,
                "session_index": index, "normalized_calendar_sha256": calendar_sha,
                "d1_source_sha256": d1_sha,
            }
            try:
                instrument = market_data.resolve("Si", as_of=date.fromisoformat(trade_date))
                record["secid"] = instrument.secid
                record["d1_exact_contract_present"] = (instrument.secid, trade_date) in d1_days
                official_sessions = set(by_contract.get(instrument.secid, []))
                if trade_date not in official_sessions:
                    record.update({"status": "SELECTED_CONTRACT_NOT_IN_CALENDAR", "h1_rows": 0,
                                   "m15_rows": 0, "raw_m1_rows_transient": 0,
                                   "detail": "resolver-selected SECID has no activity on the official exact-contract calendar"})
                    failures += 1
                else:
                    quality = store.capture_quality(instrument, trade_date)
                    if quality is not None:
                        record.update({"status": "SKIPPED_ALREADY_CAPTURED", "source_quality": quality})
                    else:
                        capture = prepare_one_day(market_data, instrument, trade_date)
                        if capture.hourly.candles:
                            record["stored_h1"] = store.ingest(capture.hourly)
                        if capture.quarter_hour.candles:
                            record["stored_m15"] = store.ingest(capture.quarter_hour)
                        record.update({
                            "h1_rows": capture.hourly.count,
                            "m15_rows": capture.quarter_hour.count,
                            "raw_m1_rows_transient": capture.minute_rows_seen,
                            "incomplete_m15_buckets": capture.incomplete_m15_buckets,
                            "skipped_h1_bars": capture.skipped_h1_bars,
                            "availability_claim": "SOURCE_RECEIVED_NOT_PIT_VERIFIED",
                        })
                        if not capture.hourly.count or not capture.quarter_hour.count:
                            record["status"] = "MISSING_INPUTS"
                            record["detail"] = "at least one required timeframe returned no admissible rows"
                            failures += 1
                        else:
                            store.mark_captured(
                                instrument, trade_date, h1_rows=capture.hourly.count,
                                m15_rows=capture.quarter_hour.count,
                                raw_m1_rows=capture.minute_rows_seen,
                                incomplete_m15_buckets=capture.incomplete_m15_buckets,
                                skipped_h1_bars=capture.skipped_h1_bars,
                            )
                            record["status"] = ("CAPTURED_WITH_GAPS"
                                if capture.incomplete_m15_buckets or capture.skipped_h1_bars
                                else "CAPTURED_SOURCE_RESPONSE")
                            if record["status"] == "CAPTURED_WITH_GAPS":
                                failures += 1
            except Exception as exc:  # retain a gap receipt and continue the bounded batch
                record.update({"status": "ERROR", "error": type(exc).__name__ + ": " + str(exc)[:300]})
                failures += 1
            _append_ledger(ledger_path, record)
            status = str(record["status"])
            status_counts[status] = status_counts.get(status, 0) + 1
            _atomic_json(progress_path, {
                "schema": _PROGRESS_SCHEMA, "from_date": first, "till_date": last,
                "d1_archive_sha256": d1_sha, "research_archive": str(args.archive.resolve()),
                "normalized_calendar_sha256": calendar_sha, "next_index": index + 1,
                "last_processed_session": trade_date,
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            })
            print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
    finally:
        store.close()

    more = stop < len(sessions)
    print(json.dumps({
        "status": "MORE_SESSIONS_REMAIN" if more else "CALENDAR_COMPLETE",
        "symbol": "Si", "processed_this_batch": stop - cursor,
        "next_session": sessions[stop] if more else None,
        "calendar_sessions_total": len(sessions), "status_counts": status_counts,
        "issues_this_batch": failures, "raw_M1_persisted": False,
    }, ensure_ascii=False, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
