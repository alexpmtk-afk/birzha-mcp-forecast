"""Read-only validation of an isolated GOLD one-day research DuckDB.

This utility never downloads prices or writes to the database. It creates only
a JSON report next to the archive so a failed/partial Actions run has evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

import duckdb

DAY = "2025-06-09"
SECID = "GDM5"
EXPECTED = {"H1": 16, "M15": 55}
SOURCE = {"H1": "MOEX_NATIVE_H1", "M15": "MOEX_M1_AGGREGATED_M15"}


def validate(path: Path, capture_exit: int) -> tuple[dict, bool]:
    result = {
        "instrument": "GOLD",
        "secid": SECID,
        "date": DAY,
        "capture_exit_code": capture_exit,
        "research_only": True,
        "historical_PIT_verified": False,
        "status": "INVALID",
        "errors": [],
    }
    errors: list[str] = result["errors"]
    if not path.is_file() or path.stat().st_size <= 0:
        errors.append("research DuckDB missing or empty")
        return result, False

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    result["archive_filename"] = path.name
    result["archive_bytes"] = path.stat().st_size
    result["archive_sha256"] = digest.hexdigest()

    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            marker = con.execute(
                "SELECT value FROM research_intraday_meta WHERE key='archive_id'"
            ).fetchone()
            result["archive_marker"] = marker[0] if marker else None
            if marker != ("BIRZHA_RESEARCH_INTRADAY_2025_2026_V1",):
                errors.append("research archive marker mismatch")
            rows = con.execute("""
                SELECT symbol, secid, engine, market, board, timeframe,
                       begin, end_time, trade_date, open, high, low, close,
                       volume, value, source_kind, normalized_sha256
                FROM research_intraday_candles ORDER BY timeframe, begin
            """).fetchall()
            result["total_candle_rows"] = len(rows)
            counts = {"H1": 0, "M15": 0}
            samples = {"H1": [], "M15": []}
            seen = set()
            for row in rows:
                (symbol, secid, engine, market, board, tf, start, end,
                 trade_date, o, h, l, c, volume, value, source, sha) = row
                if tf in counts:
                    counts[tf] += 1
                else:
                    errors.append("unexpected timeframe")
                    continue
                if (secid, tf, start) in seen:
                    errors.append("duplicate key")
                seen.add((secid, tf, start))
                if (symbol, secid, engine, market, board, trade_date, source) != (
                    "GOLD", SECID, "futures", "forts", "RFUD", DAY, SOURCE[tf]
                ):
                    errors.append(f"instrument/board/day/source mismatch {tf} {start}")
                try:
                    begin_dt = datetime.fromisoformat(start)
                    end_dt = datetime.fromisoformat(end)
                    span = (end_dt - begin_dt).total_seconds()
                    valid_span = {3599, 3600} if tf == "H1" else {899, 900}
                    if (begin_dt.tzinfo is None or end_dt.tzinfo is None or
                            begin_dt.utcoffset().total_seconds() != 10800 or
                            end_dt.utcoffset().total_seconds() != 10800 or
                            begin_dt.date().isoformat() != DAY or
                            span not in valid_span or
                            (tf == "M15" and (begin_dt.minute % 15 or begin_dt.second)) or
                            (tf == "H1" and (begin_dt.minute or begin_dt.second))):
                        errors.append(f"bad timeframe/timezone interval {tf} {start}")
                except Exception as ex:
                    errors.append(f"invalid timestamp {tf}: {type(ex).__name__}")
                if not all(type(v) in (float, int) and math.isfinite(v) and v > 0
                           for v in (o, h, l, c)):
                    errors.append(f"invalid OHLC {tf} {start}")
                elif not l <= min(o, c) <= max(o, c) <= h:
                    errors.append(f"OHLC relation violation {tf} {start}")
                if any(v is not None and (not math.isfinite(v) or v < 0)
                       for v in (volume, value)):
                    errors.append(f"negative/nonfinite volume or value {tf} {start}")
                if not isinstance(sha, str) or len(sha) != 64:
                    errors.append(f"invalid normalized SHA-256 {tf} {start}")
                if len(samples[tf]) < 3:
                    samples[tf].append({
                        "begin": start, "end": end,
                        "open": o, "high": h, "low": l, "close": c,
                        "volume": volume, "value": value,
                    })
            result["counts"] = counts
            result["samples"] = samples
            for tf, expected in EXPECTED.items():
                if counts[tf] != expected:
                    errors.append(f"{tf}: expected {expected}, observed {counts[tf]}")
            cp = con.execute("""
                SELECT symbol, secid, trade_date, h1_rows, m15_rows, raw_m1_rows,
                       quality, incomplete_m15_buckets, skipped_h1_bars
                FROM research_intraday_captured_days
            """).fetchall()
            result["checkpoints"] = [
                dict(zip(("symbol", "secid", "date", "H1", "M15", "M1_transient",
                          "quality", "incomplete_M15", "skipped_H1"), row))
                for row in cp
            ]
            if len(cp) != 1:
                errors.append("expected one checkpoint")
            else:
                sym, sec, day, h1, m15, m1, quality, incomplete, skipped = cp[0]
                if (sym, sec, day, h1, m15) != ("GOLD", SECID, DAY, counts["H1"], counts["M15"]):
                    errors.append("checkpoint identity/count mismatch")
                if quality != "CAPTURED_WITH_GAPS" or incomplete < 1 or m1 < counts["M15"] * 15:
                    errors.append("unexpected checkpoint source quality or M1 count")
                result["source_completeness"] = quality
        finally:
            con.close()
    except Exception as exc:
        errors.append(f"read-only verification error: {type(exc).__name__}: {str(exc)[:300]}")
    ok = not errors
    result["database_verified"] = ok
    result["status"] = ("SOURCE_PARTIAL_DATABASE_VERIFIED" if ok and capture_exit == 1
                        else "SOURCE_RECEIPT_DATABASE_VERIFIED" if ok and capture_exit == 0
                        else "INVALID")
    return result, ok


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--capture-exit", type=int, required=True)
    args = p.parse_args()
    report_path = args.archive.parent / "verification.json"
    try:
        report, ok = validate(args.archive, args.capture_exit)
    except Exception as exc:
        report, ok = {"status": "INVALID", "errors": [
            f"validator error: {type(exc).__name__}: {str(exc)[:300]}"]}, False
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
