"""Compare SBProX observations with SHA-verified, already stored MOEX ZIPs."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from zipfile import BadZipFile, ZipFile

from birzha.providers.sbprox_ri import MOSCOW, sha256

FIELDS = ("open", "high", "low", "close", "volume")


def load_zip(path, secid):
    with ZipFile(path) as archive:
        names = [n for n in archive.namelist() if n.endswith(".json") and n.rsplit("/", 1)[-1] != "manifest.json"]
        if len(names) != 1:
            raise ValueError("unexpected archive member list")
        with archive.open(names[0]) as stream:
            payload = json.load(stream)
    if payload.get("exact_secid", payload.get("secid")) != secid:
        raise ValueError("archive SECID differs from index")
    return payload


def moscow(value):
    t = datetime.fromisoformat(value)
    return t.astimezone(MOSCOW) if t.tzinfo else t.replace(tzinfo=MOSCOW)


def key_for(tf, start, session=None):
    if tf == "D1":
        return session
    if tf == "M1":
        return start.isoformat()
    step = 15 if tf == "M15" else 60
    return start.replace(minute=start.minute // step * step, second=0, microsecond=0).isoformat()


def aggregate(rows):
    return [rows[0][0], max(r[1] for r in rows), min(r[2] for r in rows),
            rows[-1][3], sum(r[4] for r in rows)]


def compare_zip_archives(db, archive_dir, index, report, start, through):
    expected = defaultdict(dict)
    zip_audit = []
    for index_row in index[1:]:
        secid, month = index_row[:2]
        if not month.startswith("2026-"):
            continue
        path = archive_dir / f"RI_{secid}_{month}_SOURCE_M1_OHLCV.zip"
        row = dict(secid=secid, month=month, file_id=index_row[6], sha256=index_row[8])
        zip_audit.append(row)
        try:
            if sha256(path) != row["sha256"]:
                raise ValueError("zip SHA-256 mismatch")
            payload = load_zip(path, secid)
            raw, derived = payload.get("raw", {}), payload.get("derived", {})
            lists = {tf: raw.get(tf) or derived.get(tf) for tf in ("M1", "H1", "D1")}
            lists["M15"] = derived.get("M15")
            supported = [tf for tf, items in lists.items() if isinstance(items, list) and items]
            if "M1" not in supported:
                raise ValueError("unsupported M1 archive structure")
            local = {}
            for tf in supported:
                for item in lists[tf]:
                    t = moscow(item["begin"])
                    if start <= t.date() <= through:
                        key = key_for(tf, t, t.date().isoformat())
                        values = [item[f] for f in FIELDS]
                        if (tf, key) in local:
                            raise ValueError(f"duplicate archive key {tf} {key}")
                        if key in expected[secid, tf] and expected[secid, tf][key][0] != values:
                            raise ValueError(f"conflicting reference key {tf} {key}")
                        local[tf, key] = values
            for (tf, key), values in local.items():
                expected[secid, tf][key] = (values, row["sha256"])
            row.update(status="VERIFIED", periods=supported)
        except (OSError, BadZipFile, KeyError, TypeError, ValueError) as exc:
            row.update(status="UNVERIFIED", reason=str(exc))
    observed = defaultdict(lambda: defaultdict(list))
    for secid, start_text, session, *values in db.execute(
        "SELECT secid,minute_start,trade_session_date,open,high,low,close,volume "
        "FROM bars ORDER BY secid,minute_start"
    ).fetchall():
        start_time = moscow(start_text)
        for tf in ("M1", "M15", "H1", "D1"):
            key = key_for(tf, start_time, str(session) if session else None)
            if key is not None:
                observed[secid, tf][key].append(values)
    totals = defaultdict(Counter)
    with report.open("w", encoding="utf-8") as out:
        for secid, tf in sorted(expected.keys() | observed.keys()):
            ref, obs = expected[secid, tf], observed[secid, tf]
            for key in sorted(ref.keys() | obs.keys()):
                v, source = ref.get(key, (None, None))
                actual = aggregate(obs[key]) if key in obs else None
                status = ("UNVERIFIED" if v is None else "MISSING" if actual is None else
                          "MATCH" if v == actual else "MISMATCH")
                totals[secid, tf][status] += 1
                out.write(json.dumps(dict(secid=secid, period=tf, key=key, status=status,
                                          sbprox=actual, moex=v, reference_sha256=source),
                                     separators=(",", ":")) + "\n")
    return {"zip_audit": zip_audit, "counts": {f"{s}/{tf}": dict(c) for (s, tf), c in sorted(totals.items())}}
