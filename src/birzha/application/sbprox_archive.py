"""Offline staging and read-only query for the RI SBProX research archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from birzha.providers.sbprox_ri import FILENAME, MOSCOW, read_cache, sha256

SCHEMA = "RI_SBPROX_2026_V1"
BUILD_REVISION = "2026-11-09.2"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def _snapshot(path, rows):
    path.write_text("".join(_json(row) + "\n" for row in rows), encoding="utf-8")


def evidence(root):
    contracts = {r[0]: r[1:] for r in _load(root / "contracts.jsonl")[1:]}
    sessions = {(r[0], r[1]): r[2:] for r in _load(root / "sessions.jsonl")[1:]}
    return contracts, sessions


def trade_date(bar, sessions):
    day = bar.start.date().isoformat()
    entry = sessions.get((bar.secid, day)) or sessions.get(("ALL", day))
    if not entry:
        return None, "UNVERIFIED"
    _, kind, evening, day_phase, evening_phase, quality = entry
    if kind == "CLOSED":
        raise ValueError("minutes on calendar CLOSED day")
    evening_phase_active = bool(evening and bar.start.strftime("%H:%M") >= evening)
    session = evening_phase if evening_phase_active else day_phase
    return session or None, quality if session else "UNVERIFIED"


def _check_manifest(root, manifest):
    for name, digest in manifest["checksums"].items():
        path = (root / name).resolve()
        if root.resolve() not in path.parents or not path.is_file() or sha256(path) != digest:
            raise ValueError(f"published generation has a checksum mismatch: {root}")


def stage(path, staging, file_digest, calendar_digest, contracts, sessions):
    """A successful file is an immutable, reusable checkpoint."""
    checkpoint = staging / hashlib.sha256(f"{file_digest}-{calendar_digest}".encode()).hexdigest()
    legacy_checkpoint = staging / f"{file_digest}-{calendar_digest}"
    if not checkpoint.exists() and legacy_checkpoint.is_dir():
        checkpoint = legacy_checkpoint
    if checkpoint.exists():
        manifest = _load(checkpoint / "file.json")[0]
        _check_manifest(checkpoint, manifest)
        if manifest["name"] != path.name:
            raise ValueError("same bytes supplied under a different source date")
        return checkpoint, manifest
    with tempfile.TemporaryDirectory(dir=staging, prefix="pending-") as tmp:
        work = Path(tmp) / "ready"
        work.mkdir()
        digest = hashlib.sha256()
        total = dict(minutes=0, levels=0, volume=0, ask=0, bid=0, delta=0)
        totals = defaultdict(lambda: dict.fromkeys(total, 0))
        first = last = None
        with (work / "minutes.jsonl").open("w", encoding="utf-8") as m, (
            work / "levels.jsonl"
        ).open("w", encoding="utf-8") as p:
            for bar in read_cache(path, digest):
                day = bar.start.date().isoformat()
                limits = contracts.get(bar.secid)
                if not limits or not limits[0] <= day <= limits[1]:
                    raise ValueError(f"line {bar.line}: SECID outside verified contract validity")
                session, quality = trade_date(bar, sessions)
                key = dict(secid=bar.secid, minute_start=bar.start.isoformat(),
                           source_sha256=file_digest, source_line=bar.line)
                sides = dict(volume=bar.volume, ask=bar.ask, bid=bar.bid, delta=bar.delta)
                minute = dict(**key, **sides, minute_end=bar.end.isoformat(),
                              calendar_date=day, trade_session_date=session, session_quality=quality,
                              instrument_root="RI", provider="SBPROX", raw_symbol="rsRTS",
                              raw_series=bar.series, available_at="UNKNOWN",
                              open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                              raw_fields=bar.raw)
                m.write(_json(minute) + "\n")
                for level in bar.levels:
                    p.write(_json(dict(**key, price=level.price, volume=level.volume,
                                       ask=level.ask, bid=level.bid, delta=level.delta,
                                       raw_fields=level.raw)) + "\n")
                counts = dict(**sides, minutes=1, levels=len(bar.levels))
                for k, value in counts.items():
                    total[k] += value
                    totals[bar.secid][k] += value
                first = min(first or key["minute_start"], key["minute_start"])
                last = max(last or key["minute_start"], key["minute_start"])
        if not total["minutes"] or digest.hexdigest() != file_digest:
            raise ValueError("source changed or yielded no records")
        shutil.copyfile(path, work / "raw")
        if sha256(work / "raw") != file_digest:
            raise ValueError("source changed during raw copy")
        result = dict(name=path.name, first=first, last=last, counts=total, contracts=totals,
                      checksums={f: sha256(work / f) for f in ("raw", "minutes.jsonl", "levels.jsonl")})
        _snapshot(work / "file.json", [result])
        work.rename(checkpoint)
    return checkpoint, result


def inventory(source, output, reference, start, through):
    contracts, sessions = evidence(reference)
    calendar_digest = hashlib.sha256(
        (SCHEMA + sha256(reference / "sessions.jsonl") + sha256(reference / "contracts.jsonl")).encode()
    ).hexdigest()
    staging = output / ".checkpoints"
    staging.mkdir(parents=True, exist_ok=True)
    records, ready = [], []
    actual = {p.stem: p for p in source.glob("*.locchache") if FILENAME.fullmatch(p.name)}
    day = start
    observed_days = {r[1] for r in _load(reference / "sessions.jsonl")[1:]
                     if r[0] != "ALL" and r[2] == "OBSERVED_EXACT_SECID"}
    while day <= through:
        iso = day.isoformat()
        day += timedelta(days=1)
        path = actual.get(iso)
        schedule = sessions.get(("ALL", iso))
        observed = iso in observed_days
        closed = schedule and schedule[1] == "CLOSED" and not observed
        record = dict(date=iso, path=str(path) if path else None)
        records.append(record)
        if not path or not path.stat().st_size:
            record["size"] = 0 if path else None
            record["status"] = ("NO_TRADING_CONFIRMED" if closed else
                                "TRADING_DAY_MISSING_DATA" if observed else
                                "ZERO_BYTE_UNVERIFIED" if path else "NO_FILE_UNVERIFIED")
            continue
        try:
            record.update(size=path.stat().st_size, sha256=sha256(path))
            checkpoint, result = stage(path, staging, record["sha256"], calendar_digest, contracts, sessions)
        except (ValueError, OSError) as exc:
            record.update(status="QUARANTINE", reason=str(exc))
            continue
        record.update({k: v for k, v in result.items() if k != "checksums"}, status="PARSED")
        ready.append((checkpoint, record))
    return records, ready


def compare_pilot(db, reference):
    rows = _load(reference / "pilot_h1.jsonl")
    result = []
    for row in rows:
        start = datetime.fromisoformat(row[3])
        end = start.replace(minute=59, second=59, microsecond=999000)
        actual = db.execute("""
            SELECT first(open ORDER BY minute_start), max(high), min(low),
                   last(close ORDER BY minute_start), sum(volume), count(*)
            FROM bars WHERE secid=? AND minute_start BETWEEN ? AND ?
        """, [row[6], start.isoformat(), end.isoformat()]).fetchone()
        expected = row[7:12]
        status = ("MISSING" if not actual[-1] else
                  "MATCH" if list(actual[:5]) == expected else "MISMATCH")
        result.append(dict(key=row[0], status=status, expected=expected, observed=list(actual[:5]),
                           minute_count=actual[-1], reference_complete=row[12]))
    return result


def _copy(db, query, path, parameters=None):
    quoted_path = str(path).replace("'", "''")
    db.execute(f"COPY ({query}) TO '{quoted_path}' (FORMAT PARQUET, COMPRESSION ZSTD)",
               parameters or [])
    if not path.is_file():
        raise ValueError(f"Parquet output missing: {path.name}")


def build(source, output, reference, start, through, moex_dir=None):
    import duckdb

    source, output, reference = (p.resolve() for p in (source, output, reference))
    if not source.is_dir() or source == output or source in output.parents or output in source.parents:
        raise ValueError("source must exist and output must be outside it")
    output.mkdir(parents=True, exist_ok=True)
    records, ready = inventory(source, output, reference, start, through)
    evidence_hashes = {p.name: sha256(p) for p in sorted(reference.glob("*.jsonl"))}
    if moex_dir:
        evidence_hashes["MOEX_ZIPS"] = {
            p.name: sha256(p) for p in sorted(moex_dir.glob("*2026*.zip"))
        }
    generation = hashlib.sha256(_json([SCHEMA, BUILD_REVISION, records, evidence_hashes]).encode()).hexdigest()
    final = output / generation
    if final.exists():
        manifest = _load(final / "manifest.json")[0]
        _check_manifest(final, manifest)
        return final, "UNCHANGED"
    if not ready:
        raise ValueError("no validated non-empty files")
    with tempfile.TemporaryDirectory(dir=output, prefix="pending-") as tmp:
        work = Path(tmp) / "ready"
        for sub in ("RAW", "NORMALIZED", "DERIVED", "REPORTS"):
            (work / sub).mkdir(parents=True)
        with duckdb.connect() as db:
            for view, name in (("bars", "minutes.jsonl"), ("levels", "levels.jsonl")):
                files = [str(p / name) for p, _ in ready]
                db.execute(f"CREATE TABLE {view} AS SELECT * FROM read_json(?, format='newline_delimited', "
                           "sample_size=-1, timestampformat='iso')", [files])
            db.execute("ALTER TABLE bars ALTER minute_start TYPE VARCHAR")
            db.execute("ALTER TABLE levels ALTER minute_start TYPE VARCHAR")
            conflicts = db.execute("""SELECT secid, minute_start, count(*), list(source_sha256)
                                       FROM bars GROUP BY ALL HAVING count(*) > 1""").fetchall()
            if conflicts:
                raise ValueError(f"conflicting minute keys; generation withheld ({len(conflicts)} keys)")
            pilot = compare_pilot(db, reference)
            _snapshot(work / "REPORTS" / "h1_pilot.jsonl", pilot)
            for secid, month in db.execute(
                "SELECT DISTINCT secid, substr(minute_start, 1, 7) FROM bars ORDER BY ALL"
            ).fetchall():
                scope = "WHERE secid=? AND substr(minute_start,1,7)=?"
                for table, stem, order in (("bars", "M1_BARS", "minute_start"),
                                           ("levels", "PRICE_LEVELS", "minute_start, price")):
                    _copy(db, f"SELECT * FROM {table} {scope} ORDER BY {order}",
                          work / "NORMALIZED" / f"{stem}_{secid}_{month}.parquet", [secid, month])
            write_features(db, work / "DERIVED")
            if moex_dir:
                from birzha.application.sbprox_moex_comparison import compare_zip_archives

                result = compare_zip_archives(db, moex_dir, _load(reference / "m1_index.jsonl"),
                                              work / "REPORTS" / "moex_comparison.jsonl", start, through)
                _snapshot(work / "REPORTS" / "moex_summary.jsonl", [result])
        for checkpoint, record in ready:
            shutil.copyfile(checkpoint / "raw", work / "RAW" / record["name"])
        _snapshot(work / "REPORTS" / "inventory.jsonl", records)
        checksums = {p.relative_to(work).as_posix(): sha256(p)
                     for p in sorted(work.rglob("*")) if p.is_file()}
        manifest = dict(schema=SCHEMA, revision=BUILD_REVISION, generation=generation, coverage="PARTIAL",
                        source="SBPROX", first_seen=datetime.now(timezone.utc).isoformat(),
                        available_at="UNKNOWN", evidence=evidence_hashes, checksums=checksums)
        _snapshot(work / "manifest.json", [manifest])
        work.rename(final)
    return final, "CREATED"


def write_features(db, directory):
    # Only session-assigned rows can have a session-reset delta; it remains observed coverage.
    for tf, key in (("M1", "minute_start"), ("M15", "time_bucket(INTERVAL '15 minutes', minute_start::TIMESTAMP)"),
                    ("H1", "date_trunc('hour', minute_start::TIMESTAMP)"),
                    ("SESSION", "trade_session_date")):
        where = "WHERE trade_session_date IS NOT NULL" if tf == "SESSION" else ""
        _copy(db, f"""WITH src AS (SELECT *, {key} AS bucket FROM bars {where}),
            grouped AS (
              SELECT secid, trade_session_date, session_quality, bucket,
                     min(minute_start) AS first_observed, max(minute_end) AS observed_through,
                     count(*) AS minutes, sum(volume) AS volume, sum(ask) AS ask,
                     sum(bid) AS bid, sum(delta) AS delta
              FROM src GROUP BY ALL
            )
            SELECT *, 'OBSERVED_PARTIAL_OR_UNVERIFIED' AS coverage, 'UNKNOWN' AS available_at,
                   CASE WHEN trade_session_date IS NOT NULL THEN
                     sum(delta) OVER (PARTITION BY secid, trade_session_date ORDER BY first_observed
                                      ROWS UNBOUNDED PRECEDING)
                   END AS observed_cumulative_delta
            FROM grouped ORDER BY secid, first_observed""", directory / f"FLOW_{tf}.parquet")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "output", "reference"):
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--moex-dir", type=Path)
    p.add_argument("--start", type=date.fromisoformat, default=date(2026, 1, 1))
    p.add_argument("--through", type=date.fromisoformat, required=True)
    args = p.parse_args()
    if not date(2026, 1, 1) <= args.start <= args.through <= date(2026, 12, 31):
        p.error("only an explicit completed 2026 calendar interval is allowed")
    if args.through >= datetime.now(MOSCOW).date():
        p.error("the current Moscow calendar date is not eligible")
    path, state = build(args.source, args.output, args.reference, args.start, args.through, args.moex_dir)
    print(f"{state} {path}")


if __name__ == "__main__":
    main()
