"""Research-only monthly GOLD capture: native H1 and strict M1->M15.

Use approved existing GOLD --mode ingest for the most liquid contract, then
independently capture all other exact listed GD quarterly contracts. Never
touch production DuckDB, mirrors, Home services or other markets.
"""
from __future__ import annotations

import argparse
import calendar
import csv
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback
from zoneinfo import ZoneInfo

import duckdb

from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.research_intraday_fetch import prepare_one_day
from birzha.providers.moex_history import _pick_instrument

MOSCOW = ZoneInfo("Europe/Moscow")
SECID_PATTERN = re.compile(r"^GD[HMUZ]\d{1,2}$")
ARCHIVE_NAME = "birzha_intraday_research_2025_2026.duckdb"
HEADERS = (
    "record_key", "calendar_date", "trade_session_date", "session_id",
    "bar_start_time", "bar_end_time", "available_at", "available_at_confidence",
    "contract_code", "open", "high", "low", "close", "volume",
    "number_of_trades", "turnover", "is_complete", "source_interval",
    "source_candles", "source_id", "research_status", "normalized_sha256",
    "source_archive_sha256", "capture_day_status", "source_kind", "caveat",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def exact_contracts(service: MarketDataService, day: date) -> list:
    """Read contracts quoted on this date from MOEX, not from a rolling symbol."""
    payload = service.provider._request(
        "/history/engines/futures/markets/forts/securities.json",
        {"iss.meta": "off", "iss.only": "history",
         "history.columns": (
             "TRADEDATE,SECID,BOARDID,ASSETCODE,VALUE,VOLUME,"
             "OPENPOSITIONVALUE,OPENPOSITION,SHORTNAME,"
             "LASTTRADEDATE,LASTDELDATE,MINSTEP,STEPPRICE,LOTVOLUME,CURRENCYID"),
         "date": day.isoformat(), "assetcode": "GOLD"},
    ).json()
    rows = service.provider._table(payload, "history")
    selected = {}
    for item in rows:
        secid = str(item.get("SECID") or "")
        asset = str(item.get("ASSETCODE") or "")
        tradedate = str(item.get("TRADEDATE") or "")[:10]
        if (SECID_PATTERN.fullmatch(secid) and asset.upper() == "GOLD" and
                tradedate == day.isoformat() and
                str(item.get("BOARDID") or "RFUD") == "RFUD"):
            selected[secid] = _pick_instrument("GOLD", day, [item])
    return [selected[secid] for secid in sorted(selected)]


def monthly_capture(month: str, archive_path: Path, output: Path) -> int:
    first = date.fromisoformat(month + "-01")
    if month < "2025-01" or month > "2026-10" or first.isoformat() != month + "-01":
        raise ValueError("research date must be 2025-01..2026-10")
    if archive_path.name != ARCHIVE_NAME or archive_path.is_symlink():
        raise ValueError("refuse non-dedicated archive")
    output.mkdir(parents=True, exist_ok=True)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    days = [date(first.year, first.month, n) for n in range(
        1, calendar.monthrange(first.year, first.month)[1] + 1)
        if date(first.year, first.month, n).weekday() < 5 and
        date(first.year, first.month, n) < datetime.now(MOSCOW).date()]
    receipts = []
    service = MarketDataService.default()
    for day in days:
        iso = day.isoformat()
        print(f"DATE_START {iso}", flush=True)
        command = [
            sys.executable, "-m", "scripts.g2_intraday_research_archive",
            "--mode", "ingest", "--date", iso, "--markets", "GOLD",
            "--archive", str(archive_path), "--ack-research-write",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=240)
        selected = None
        root_observation = None
        for line in completed.stdout.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("market") == "GOLD":
                root_observation = item
                selected = item.get("secid")
        receipt = {
            "date": iso, "primary_returncode": completed.returncode,
            "primary": root_observation,
            "primary_stderr": completed.stderr[-500:],
            "other_contracts": [],
        }
        if completed.returncode not in (0, 1) or not root_observation:
            receipt["status"] = "PRIMARY_ERROR"
        else:
            receipt["status"] = "PARTIAL_SOURCE_GAPS" if completed.returncode == 1 else "SOURCE_RECEIPT"
        try:
            listed = exact_contracts(service, day)
            receipt["listed_secids"] = [i.secid for i in listed]
            for instrument in listed:
                if instrument.secid == selected:
                    continue
                item = {"secid": instrument.secid}
                try:
                    archive = ResearchIntradayArchive(str(archive_path))
                    try:
                        quality = archive.capture_quality(instrument, iso)
                        if quality is not None:
                            item["status"] = "SKIPPED_CHECKPOINT_" + quality
                            continue
                        observed = prepare_one_day(service, instrument, iso)
                        item["source_h1"] = observed.hourly.count
                        item["source_m1"] = observed.minute_rows_seen
                        item["source_m15"] = observed.quarter_hour.count
                        item["incomplete_m15"] = observed.incomplete_m15_buckets
                        item["skipped_h1"] = observed.skipped_h1_bars
                        for series in (observed.hourly, observed.quarter_hour):
                            if series.candles:
                                archive.ingest(series)
                        if observed.hourly.count and observed.quarter_hour.count:
                            archive.mark_captured(
                                instrument, iso, h1_rows=observed.hourly.count,
                                m15_rows=observed.quarter_hour.count,
                                raw_m1_rows=observed.minute_rows_seen,
                                incomplete_m15_buckets=observed.incomplete_m15_buckets,
                                skipped_h1_bars=observed.skipped_h1_bars,
                            )
                        item["status"] = (
                            "PARTIAL_SOURCE_GAPS" if observed.incomplete_m15_buckets or
                            observed.skipped_h1_bars else
                            "SOURCE_RECEIPT" if observed.hourly.count and
                            observed.quarter_hour.count else "MISSING_INPUTS"
                        )
                    finally:
                        archive.close()
                except Exception as exc:
                    item["status"] = "ERROR"
                    item["error"] = type(exc).__name__ + ": " + str(exc)[:260]
                finally:
                    receipt["other_contracts"].append(item)
        except Exception as exc:
            receipt["list_contracts_error"] = type(exc).__name__ + ": " + str(exc)[:260]
        receipts.append(receipt)
        with (output / "day_receipts.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), flush=True)

    # Original archive is closed by all writers. The following query is read-only.
    meta = {"month": month, "date_days_scanned_weekdays": len(days),
            "source": "MOEX ISS futures/forts RFUD exact GD contracts",
            "pit": "NOT_VERIFIED", "calendar": "WEEKDAY_SCAN_NOT_CERTIFIED",
            "price_archive": archive_path.name, "receipts": receipts, "errors": []}
    if not archive_path.exists():
        meta["errors"].append("archive is missing")
        (output / "manifest.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
        return 2

    con = duckdb.connect(str(archive_path), read_only=True)
    try:
        rows = con.execute("""
            SELECT symbol,secid,engine,market,board,timeframe,begin,end_time,
                   trade_date,open,high,low,close,volume,value,source_kind,normalized_sha256
            FROM research_intraday_candles ORDER BY timeframe,secid,begin
        """).fetchall()
        cp = con.execute("""
            SELECT secid,trade_date,h1_rows,m15_rows,quality,
                   incomplete_m15_buckets,skipped_h1_bars
            FROM research_intraday_captured_days
        """).fetchall()
    finally:
        con.close()
    meta["archive_bytes"] = archive_path.stat().st_size
    meta["archive_sha256"] = sha256(archive_path)
    meta["row_count"] = len(rows)
    meta["counts"] = {}
    meta["contracts"] = sorted(set(row[1] for row in rows))
    meta["checkpoint_count"] = len(cp)
    cp_map = {(r[0], r[1]): r[4] for r in cp}
    keys = set()
    with (output / "H1.csv").open("w", newline="", encoding="utf-8") as h1out, (
         output / "M15.csv").open("w", newline="", encoding="utf-8") as m15out:
        writers = {"H1": csv.writer(h1out), "M15": csv.writer(m15out)}
        for w in writers.values():
            w.writerow(HEADERS)
        for row in rows:
            (sym,sec,engine,market,board,tf,start,end,day,o,h,l,c,vol,
             value,source,fingerprint) = row
            key = (sec,tf,start)
            if key in keys: meta["errors"].append("duplicate primary key")
            keys.add(key)
            if not (sym == "GOLD" and SECID_PATTERN.fullmatch(sec) and
                    engine == "futures" and market == "forts" and board == "RFUD" and
                    day.startswith(month) and tf in writers and
                    l <= min(o,c) <= max(o,c) <= h):
                meta["errors"].append(f"bad instrument/OHLC row: {key}")
            meta["counts"][tf] = meta["counts"].get(tf, 0) + 1
            iso_start = datetime.fromisoformat(start)
            iso_end = datetime.fromisoformat(end)
            span = (iso_end - iso_start).total_seconds()
            if (iso_start.utcoffset() is None or
                iso_start.utcoffset().total_seconds() != 10800 or
                span not in ((3599,3600) if tf == "H1" else (899,900))):
                meta["errors"].append(f"bad timestamp: {key}")
            if vol is not None and vol < 0:
                meta["errors"].append(f"negative volume: {key}")
            status = cp_map.get((sec,day), "NOT_CHECKPOINTED")
            record = [
                f"GOLD|{tf}|{sec}|{start}", day, "", "", start, end, "",
                "UNKNOWN",sec,o,h,l,c,vol,"","",True,
                "H1" if tf == "H1" else "M1",
                1 if tf == "H1" else 15,"MOEX_ISS",
                "RESEARCH_ONLY_NOT_PIT_VERIFIED",fingerprint,
                meta["archive_sha256"],status,source,
                "Historical first receipt and exact exchange sessions not independently verified",
            ]
            writers[tf].writerow(record)
    for fn in ("H1.csv", "M15.csv"):
        meta[fn + "_sha256"] = sha256(output / fn)
    meta["has_source_gaps"] = any(
        x["status"] != "SOURCE_RECEIPT" or
        any(y.get("status") != "SOURCE_RECEIPT" and
            not str(y.get("status", "")).startswith("SKIPPED_CHECKPOINT")
            for y in x.get("other_contracts", [])) or
        x.get("list_contracts_error")
        for x in receipts)
    meta["status"] = ("INVALID" if meta["errors"] or not rows else
                      "SOURCE_PARTIAL_RESEARCH_VERIFIED" if meta["has_source_gaps"] else
                      "SOURCE_RECEIPT_NOT_PIT_VERIFIED")
    (output / "manifest.json").write_text(
        json.dumps(meta, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    print("FINAL_MANIFEST", json.dumps({
        k: meta.get(k) for k in (
            "status", "archive_sha256", "counts", "contracts",
            "checkpoint_count", "errors", "date_days_scanned_weekdays")
    }, ensure_ascii=False), flush=True)
    return 0 if meta["status"] != "INVALID" else 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--month", required=True)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        return monthly_capture(args.month, args.archive, args.output)
    except Exception as exc:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "fatal.txt").write_text(
            type(exc).__name__ + ": " + str(exc)[:400] + "\n" +
            traceback.format_exc()[-3000:], encoding="utf-8")
        print("FATAL", type(exc).__name__, str(exc)[:300], flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
