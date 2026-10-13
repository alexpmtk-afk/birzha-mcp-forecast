"""Consolidate 22 independently sourced IMOEX month partitions to ONE research DuckDB.

No network/production writes. Fails closed unless all expected months through
2026-10-12, all source files+SHA256, exact source price/contract identities,
and per-day source checkpoints agree. Never changes source partitions.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path

import duckdb

from birzha.application.research_intraday_archive import ResearchIntradayArchive

FILENAME = "birzha_intraday_research_2025_2026.duckdb"
FINAL_DAY = "2026-10-12"


def expected_months() -> tuple[str, ...]:
    return tuple(f"{year}-{month:02d}" for year in (2025, 2026)
                 for month in range(1, (13 if year == 2025 else 11)))


def checksum_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fd:
        for chunk in iter(lambda: fd.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_partition(part: Path, month: str):
    manifest = part / f"IMOEX_{month}_coverage.json"
    database = part / FILENAME
    checksums = part / f"IMOEX_{month}_SHA256SUMS.txt"
    if not all(p.is_file() and not p.is_symlink() for p in (manifest, database, checksums)):
        raise ValueError(f"missing or symlinked monthly source: {month}")
    verified = {}
    for line in checksums.read_text().splitlines():
        sha, name = line.split(maxsplit=1)
        name = name.strip().lstrip("*")
        if "/" in name or "\\" in name or name in verified:
            raise ValueError(f"invalid checksum list: {month}")
        verified[name] = sha
    if (verified.get(manifest.name) != checksum_file(manifest) or
            verified.get(database.name) != checksum_file(database)):
        raise ValueError(f"source SHA256 mismatch: {month}")
    report = json.loads(manifest.read_text())
    if report.get("schema") != "G2_IMOEX_MONTHLY_ARCHIVE_V1" or (
            report.get("symbol"), report.get("secid")) != ("IMOEX", "IMOEX"):
        raise ValueError(f"wrong market/manifest schema: {month}")
    start = f"{month}-01"
    month_end = date(int(month[:4]) + (int(month[-2:]) == 12),
                     int(month[-2:]) % 12 + 1, 1).toordinal() - 1
    end = date.fromordinal(month_end).isoformat()
    if month == "2026-10":
        end = FINAL_DAY
    if (report.get("from_date"), report.get("till_date")) != (start, end):
        raise ValueError(f"unexpected date bounds for {month}")
    days = report.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError(f"no source-candidate days for {month}")
    if (report.get("d1_source_candidate_days") != len(days)
            or report.get("days_missing_source") != 0
            or report.get("days_with_captured_price_rows") != len(days)):
        raise ValueError(f"missing, inconsistent or partial whole source days: {month}")
    seen = set()
    for item in days:
        d = item.get("date")
        if d in seen or not isinstance(d, str) or not start <= d <= end:
            raise ValueError(f"duplicate/out of range candidate: {month}")
        seen.add(d)
        if item.get("state") not in ("CAPTURED_WITH_GAPS", "CAPTURED_SOURCE_RESPONSE"):
            raise ValueError(f"source day not captured: {month} {d}")
    return database, report, seen


def merge(source_root: Path, target: Path, report_path: Path,
          months: tuple[str, ...] | None = None) -> dict:
    expected = months if months is not None else expected_months()
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("nonempty, unique expected month sequence required")
    if target.exists() or report_path.exists() or target.is_symlink() or report_path.is_symlink():
        raise ValueError("do not overwrite research database or merged manifest")
    if not target.parent.is_dir() or not report_path.parent.is_dir():
        raise ValueError("output directory must preexist")
    if target.name != FILENAME or report_path.suffix != ".json":
        raise ValueError("dedicated archive basename and JSON manifest required")
    parts = {p.name.removeprefix("imoex-intraday-").removesuffix("-duckdb"):p
             for p in source_root.glob("imoex-intraday-????-??-duckdb")
             if p.is_dir() and not p.is_symlink()}
    if set(parts) != set(expected):
        raise ValueError(f"monthly partitions missing/extra: expected={len(expected)}, got={sorted(parts)}")
    sources = [(month, *validate_partition(parts[month], month)) for month in expected]
    target_store = ResearchIntradayArchive(str(target))
    monthly = []
    try:
        for month, path, manifest, expected_days in sources:
            connection = duckdb.connect(str(path), read_only=True)
            try:
                mark = connection.execute(
                    "SELECT value FROM research_intraday_meta WHERE key='archive_id'"
                ).fetchone()
                expected_marker = "BIRZHA_RESEARCH_INTRADAY_2025_2026_V1"
                if mark != (expected_marker,):
                    raise ValueError(f"invalid research source marker: {month}")
                candles = connection.execute(
                    "SELECT * FROM research_intraday_candles ORDER BY begin"
                ).fetchall()
                checkpoints = connection.execute(
                    "SELECT * FROM research_intraday_captured_days ORDER BY trade_date"
                ).fetchall()
                if {row[2] for row in checkpoints} != expected_days:
                    raise ValueError(f"checkpoint/source day discrepancy {month}")
                if not candles or any(
                    row[0] != "IMOEX" or row[1] != "IMOEX"
                    or row[5] not in ("H1", "M15")
                    or row[8] not in expected_days
                    for row in candles
                ):
                    raise ValueError(f"candle market/timeframe/day invalid {month}")
                counts = {
                    tf: sum(1 for row in candles if row[5] == tf)
                    for tf in ("H1", "M15")
                }
                inventory = {i["timeframe"]:i["rows"] for i in manifest["inventory"]}
                if counts != inventory or min(counts.values()) <= 0:
                    raise ValueError(f"candle and manifest row totals disagree {month}")
                if any(row[0] != "IMOEX" or row[1] != "IMOEX"
                       or row[6] not in ("CAPTURED_WITH_GAPS", "CAPTURED_SOURCE_RESPONSE")
                       for row in checkpoints):
                    raise ValueError(f"unexpected checkpoint data {month}")
            finally:
                connection.close()
            target_store.conn.execute("BEGIN TRANSACTION")
            try:
                target_store.conn.executemany(
                    "INSERT INTO research_intraday_candles VALUES (" +
                    ",".join("?" for _ in candles[0]) + ")", candles,
                )
                target_store.conn.executemany(
                    "INSERT INTO research_intraday_captured_days VALUES (" +
                    ",".join("?" for _ in checkpoints[0]) + ")", checkpoints,
                )
                target_store.conn.execute("COMMIT")
            except Exception:
                target_store.conn.execute("ROLLBACK")
                raise
            monthly.append({"month":month, "source_days":len(expected_days),
                            "H1":counts["H1"], "M15":counts["M15"],
                            "captured_with_gaps":sum(r[6] == "CAPTURED_WITH_GAPS" for r in checkpoints),
                            "source_manifest_sha256":checksum_file(parts[month]/f"IMOEX_{month}_coverage.json"),
                            "source_duckdb_sha256":checksum_file(path)})
        combined = {key: sum(m[key] for m in monthly)
                    for key in ("source_days", "H1", "M15", "captured_with_gaps")}
        inventory = target_store.inventory()
        if {i["timeframe"]:i["rows"] for i in inventory} != {
            "H1":combined["H1"], "M15":combined["M15"]}:
            raise ValueError("merged archive row count does not match sum")
        cpcount = target_store.conn.execute(
            "SELECT count(*) FROM research_intraday_captured_days"
        ).fetchone()[0]
        if cpcount != combined["source_days"]:
            raise ValueError("merged checkpoint count mismatch")
    finally:
        target_store.close()
    report = {"schema":"G2_IMOEX_CONSOLIDATED_V1","symbol":"IMOEX",
              "from_date":f"{expected[0]}-01", "till_date":FINAL_DAY,
              "months":monthly, "total":combined,
              "PIT_certified":False,
              "independent_session_calendar_certified":False,
              "M15_policy":"STRICT_15_OF_15_ONLY",
              "minute_M1_persisted":False,
              "forecast_v1_bit_parity_certified":False,
              "no_trade":True, "release_blocked":True,
              "consolidated_duckdb_sha256":checksum_file(target)}
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,sort_keys=True)+"\n")
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root",type=Path,required=True)
    p.add_argument("--target",type=Path,required=True)
    p.add_argument("--report",type=Path,required=True)
    a=p.parse_args(argv)
    report=merge(a.source_root,a.target,a.report)
    print(json.dumps({"status":"MERGED_RESEARCH_NOT_PIT_CERTIFIED",
                      "total":report["total"],"months":len(report["months"]),
                      "sha256":report["consolidated_duckdb_sha256"]},sort_keys=True))


if __name__=="__main__":
    main()
