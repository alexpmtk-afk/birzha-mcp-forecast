"""SBProX source integration for the existing Google-first archive."""
import hashlib
import io
import json
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from birzha.application.sbprox_archive_reader import profile_stats
from birzha.application.sbprox_moex_comparison import FIELDS, key_for, moscow
from birzha.providers.sbprox_ri import MOSCOW, SERIES

FILE_ID = "1tjN9bHtmXHSbteD3TBCX_eiyOyz7eDwu"
FOLDER_ID = "1DMI1dZdYomr9Jm31EHrHb9Qiux5de9oT"
NAME = "RI_SBPROX_2026_MASTER.zip"
from birzha.application.model3_indicator_coverage import SBPROX_COLUMNS
OUTPUTS = SBPROX_COLUMNS


def source_contract():
    return dict(file_id=FILE_ID, folder_id=FOLDER_ID, file_name=NAME,
                provider="SBPROX", collection="RI_SBPROX_2026",
                acquisition="OWNER_LOADS_SBPROX_LOCAL_CACHE_THEN_VERIFIED_IMPORT",
                instrument="RI", exact_contracts=list(SERIES.values()),
                fields=list(OUTPUTS), open_interest="UNVERIFIED_RAW_FIELDS_ONLY",
                trades_count="UNVERIFIED_RAW_FIELDS_ONLY", available_at="UNKNOWN",
                complete_trade_tape=False, sheet_apply="SEPARATE_APPROVED_SCHEMA_AND_WRITER_REQUIRED")


@contextmanager
def open_master(source):
    body = source["body"]
    if len(body) > 128 * 1024 * 1024 or hashlib.sha256(body).hexdigest() != source["sha256"]:
        raise ValueError("SBPROX_SOURCE_HASH_OR_SIZE")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > 2000:
            raise ValueError("SBPROX_DUPLICATE_OR_EXCESSIVE_MEMBERS")
        info = archive.getinfo("MANIFEST.json")
        if info.file_size > 2 * 1024 * 1024:
            raise ValueError("SBPROX_MANIFEST_BOUND")
        manifest = json.loads(archive.read(info))
        if manifest.get("schema") != "RI_SBPROX_2026_MASTER_V1":
            raise ValueError("SBPROX_SCHEMA_UNKNOWN")
        with tempfile.TemporaryDirectory(prefix="birzha-sbprox-read-") as temp:
            paths = {}
            for table in ("M1_BARS", "PRICE_LEVELS"):
                name = "TABLES/" + table + ".parquet"
                member = archive.getinfo(name)
                if member.file_size > 256 * 1024 * 1024:
                    raise ValueError("SBPROX_TABLE_BOUND")
                path = Path(temp) / (table + ".parquet")
                digest = hashlib.sha256()
                with archive.open(member) as incoming, path.open("wb") as out:
                    while block := incoming.read(1024 * 1024):
                        digest.update(block)
                        out.write(block)
                if digest.hexdigest() != manifest["checksums"][name]:
                    raise ValueError("SBPROX_TABLE_HASH")
                paths[table] = str(path)
            with duckdb.connect() as db:
                for table, keys in (("M1_BARS", "secid,minute_start"),
                                    ("PRICE_LEVELS", "secid,minute_start,price")):
                    db.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{paths[table].replace(chr(39), chr(39)*2)}')")
                    count, unique = db.execute(
                        f"SELECT count(*),count(DISTINCT ({keys.replace('minute_start', 'minute_start::TIMESTAMPTZ')})) FROM {table}"
                    ).fetchone()
                    if count != unique or count != manifest["tables"][table]["rows"]:
                        raise ValueError("SBPROX_TABLE_COUNT_OR_DUPLICATES")
                yield db, manifest


def _window(secid, start, end, now):
    if secid not in SERIES.values():
        raise ValueError("RI_EXACT_SECID_REQUIRED")
    a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if a.tzinfo is None or b.tzinfo is None:
        raise ValueError("EXPLICIT_TIMEZONE_REQUIRED")
    a, b = a.astimezone(MOSCOW), b.astimezone(MOSCOW)
    if a.year != 2026 or b.year != 2026 or not a < b or b-a > timedelta(days=7):
        raise ValueError("BOUNDED_2026_WINDOW_REQUIRED")
    if b > now.astimezone(MOSCOW):
        raise ValueError("FUTURE_WINDOW_REJECTED")
    return a, b


CLOCK_WINDOW = "COMPLETE_CLOCK_WINDOW_RESEARCH"
MATCHED_SPARSE_WINDOW = "MATCHED_INDEXED_MOEX_M1_RESEARCH"
MATCHED_NONSTANDARD_WINDOW = "MATCHED_NONSTANDARD_MOEX_H1_RESEARCH"


def indexed_minute_reference(transport):
    """Load each needed indexed MOEX month once per calculation."""
    from birzha.storage.google_m1_bytes import validate_zip
    from birzha.storage.monthly_m1_source import _read_zip_bytes
    months = {}

    def load(secid, month, timeframe, begin):
        key = (secid, month, timeframe)
        if key not in months:
            archive = transport.read_m1("RI", secid, month)
            windows = {}
            evidence = None
            hourly = {}
            if archive and archive["indexed"]:
                rows = validate_zip(archive["body"], "RI", secid, month, archive["sha256"])
                for row in rows:
                    minute = moscow(row["begin"])
                    window = windows.setdefault(key_for(timeframe, minute), {})
                    if minute in window:
                        raise ValueError("MOEX_REFERENCE_DUPLICATE_MINUTE")
                    window[minute] = tuple(row[k] for k in FIELDS)
                evidence = dict(secid=secid, month=month, file_id=archive["file_id"],
                                sha256=archive["sha256"])
                if timeframe == "H1":
                    payload, _, _ = _read_zip_bytes(archive["body"])
                    for row in payload.get("derived", {}).get("H1", []):
                        start = moscow(row["begin"]).isoformat()
                        if start in hourly:
                            raise ValueError("MOEX_REFERENCE_DUPLICATE_HOUR")
                        hourly[start] = row
            months[key] = (windows, evidence, hourly)
        windows, evidence, hourly = months[key]
        return windows.get(begin, {}), evidence, hourly.get(begin)

    return load


def aggregate(source, secid, timeframe, start, end, now, *, reference_minutes=None):
    a, b = _window(secid, start, end, now)
    if timeframe not in ("M15", "H1", "D1"):
        raise ValueError("TIMEFRAME_REQUIRED")
    with open_master(source) as (db, manifest):
        all_rows = db.execute("""SELECT secid,minute_start,trade_session_date,session_quality,
            open,high,low,close,volume,ask,bid,delta FROM M1_BARS
            WHERE secid=? AND minute_start::TIMESTAMPTZ >= ? AND minute_start::TIMESTAMPTZ < ?
            ORDER BY minute_start""", [secid, a, b]).fetchall()
        levels = db.execute("""SELECT minute_start,price,volume FROM PRICE_LEVELS
            WHERE secid=? AND minute_start::TIMESTAMPTZ >= ? AND minute_start::TIMESTAMPTZ < ?""",
                            [secid, a, b]).fetchall()
    groups, minute_keys = {}, {}
    for row in all_rows:
        t = datetime.fromisoformat(row[1]).astimezone(MOSCOW)
        if t.second or t.microsecond:
            raise ValueError("MINUTE_ALIGNMENT_REQUIRED")
        if t + timedelta(minutes=1) > b:
            continue
        if timeframe == "D1":
            key = str(row[2]) if row[2] else None
        else:
            step = 15 if timeframe == "M15" else 60
            key = t.replace(minute=t.minute//step*step, second=0, microsecond=0).isoformat()
        if key is None:
            continue
        minute_keys[row[1]] = key
        groups.setdefault(key, dict(rows=[], profile={} ))["rows"].append(row)
    for t, price, volume in levels:
        if t in minute_keys:
            profile = groups[minute_keys[t]]["profile"]
            profile[price] = profile.get(price, 0) + volume
    output = []
    for key, group in sorted(groups.items()):
        rows, profile = group["rows"], group["profile"]
        bars = [rows[0][4], max(r[5] for r in rows), min(r[6] for r in rows),
                rows[-1][7], sum(r[8] for r in rows)]
        ask, bid, delta = (sum(r[i] for r in rows) for i in (9, 10, 11))
        if bars[4] != ask+bid or delta != ask-bid or sum(profile.values()) != bars[4]:
            raise ValueError("SBPROX_AGGREGATE_TOTALS")
        expected = 15 if timeframe == "M15" else 60
        complete = timeframe != "D1" and len(rows) == expected
        quality = CLOCK_WINDOW if complete else "PARTIAL_OR_SESSION_UNVERIFIED"
        reference = None
        archived_hour = None
        if timeframe != "D1" and not complete and reference_minutes:
            begin = datetime.fromisoformat(key)
            # A request clipped inside a clock window cannot attest that window.
            if a <= begin and begin + timedelta(minutes=expected) <= b:
                ref_rows, evidence, archived_hour = reference_minutes(secid, key[:7], timeframe, key)
                observed = {moscow(r[1]): tuple(r[4:9]) for r in rows}
                if evidence and ref_rows and observed == ref_rows:
                    quality, reference = MATCHED_SPARSE_WINDOW, evidence
        stats = profile_stats(profile) if bars[4] > 0 else None
        values = [bars[4], ask, bid, delta,
                  stats["poc"] if stats else None, stats["val"] if stats else None,
                  stats["vah"] if stats else None, json.dumps(stats["hvn"]) if stats else None,
                  json.dumps(stats["lvn"]) if stats else None, source["sha256"], quality,
                  "SBPROX_OBSERVED_PROFILE_V1"]
        output.append(dict(secid=secid, timeframe=timeframe, begin=key, minutes=len(rows),
                           ohlcv=bars, session_quality=sorted({str(r[3]) for r in rows}),
                           values=dict(zip(OUTPUTS, values)), moex_m1_reference=reference,
                           archived_moex_hour=archived_hour if reference else None,
                           last_minute=rows[-1][1]))
    return dict(source_sha256=source["sha256"], generation=manifest["generation"],
                file_id=FILE_ID, available_at="UNKNOWN", written=False, rows=output)



def verified_nonstandard_hour(row, candle, target):
    """Preserve MOEX's original hour while admitting corroborated observations."""
    proof = row.get("archived_moex_hour")
    ref = row.get("moex_m1_reference")
    if (row["timeframe"] != "H1" or not proof or not ref
            or candle.get("is_complete") not in (False, "FALSE")
            or target.get("source_complete_flag") not in (False, "FALSE")
            or proof.get("is_complete") is not False
            or proof.get("secid") != row["secid"]
            or not proof.get("normalized_sha256")
            or candle.get("normalized_sha256") != proof["normalized_sha256"]
            or candle.get("source_file_id") != ref["file_id"]
            or [proof.get(k) for k in FIELDS] != row["ohlcv"]):
        return False
    try:
        start, finish = moscow(proof["begin"]), moscow(proof["end"])
        return (start == moscow(row["begin"])
                and moscow(row["last_minute"]) <= finish < start + timedelta(hours=1)
                and finish == moscow(candle["bar_end_time"]) == moscow(target["end"]))
    except (KeyError, TypeError, ValueError):
        return False

def sheet_plan(result, candle_rows, indicator_rows):
    """A bounded review plan. No source or indicator writes."""
    if not candle_rows or not indicator_rows:
        raise ValueError("EXISTING_SHEETS_REQUIRED")
    ch, ih = candle_rows[0], indicator_rows[0]
    if len(ih) != len(set(ih)):
        raise ValueError("DUPLICATE_INDICATOR_COLUMNS")
    candles = [dict(zip(ch, row)) for row in candle_rows[1:]]
    indicators = [dict(zip(ih, row)) for row in indicator_rows[1:]]
    missing_columns = [name for name in OUTPUTS if name not in ih]
    plans = []
    for row in result["rows"]:
        def matches(item, contract_field, time_field):
            if item.get(contract_field) != row["secid"]:
                return False
            value = str(item.get(time_field, ""))
            if row["timeframe"] == "D1":
                return value[:10] == row["begin"]
            try:
                return datetime.fromisoformat(value) == datetime.fromisoformat(row["begin"])
            except ValueError:
                return False
        raw = [r for r in candles if matches(r, "contract_code", "bar_start_time")]
        target = [r for r in indicators if matches(r, "secid", "begin")]
        reasons = []
        values = dict(row["values"])
        nonstandard = (len(raw) == len(target) == 1
                       and verified_nonstandard_hour(row, raw[0], target[0]))
        if nonstandard:
            values["sbprox_quality"] = MATCHED_NONSTANDARD_WINDOW
        if len(raw) != 1 or len(target) != 1:
            reasons.append("UNIQUE_EXISTING_SOURCE_AND_INDICATOR_ROW_REQUIRED")
        elif [raw[0].get(k) for k in ("open","high","low","close","volume")] != row["ohlcv"]:
            reasons.append("CANONICAL_OHLCV_MISMATCH")
        if len(raw) == 1 and raw[0].get("is_complete") not in (True, "TRUE") and not nonstandard:
            reasons.append("CANONICAL_SOURCE_NOT_COMPLETE")
        if row["values"]["sbprox_quality"] not in (CLOCK_WINDOW, MATCHED_SPARSE_WINDOW):
            reasons.append("SOURCE_WINDOW_NOT_ATTESTED")
        if len(raw) == len(target) == 1 and "source_record_key" in ih:
            if (target[0].get("symbol") != "RI" or target[0].get("timeframe") != row["timeframe"]
                    or target[0].get("source_record_key") != raw[0].get("record_key")
                    or not raw[0].get("normalized_sha256")
                    or target[0].get("source_record_sha256") != raw[0].get("normalized_sha256")
                    or (target[0].get("source_complete_flag") not in (True, "TRUE") and not nonstandard)):
                reasons.append("INDICATOR_SOURCE_IDENTITY_OR_QUALITY_CONFLICT")
            if row["timeframe"] != "D1":
                try:
                    finish = datetime.fromisoformat(raw[0]["bar_end_time"])
                    expected = datetime.fromisoformat(row["begin"]) + timedelta(
                        minutes=15 if row["timeframe"] == "M15" else 60)
                    if finish not in (expected, expected-timedelta(seconds=1)) and not nonstandard:
                        reasons.append("CANONICAL_WINDOW_BOUNDARY_CONFLICT")
                except (KeyError, TypeError, ValueError):
                    reasons.append("CANONICAL_WINDOW_BOUNDARY_UNKNOWN")
        if missing_columns:
            reasons.append("DESTINATION_COLUMNS_REQUIRE_REVIEW")
        if len(target) == 1 and any(target[0].get(f) not in (None, "")
                                   and target[0][f] != v for f, v in values.items()):
            reasons.append("EXISTING_SBPROX_VALUE_OR_PROVENANCE_CONFLICT")
        cells = []
        if not reasons:
            for field, value in values.items():
                if target[0].get(field) in ("", None) and value is not None:
                    cells.append(dict(field=field, value=value, expected_old="EMPTY",
                                      row_index=next(i for i, r in enumerate(indicators, 1) if r is target[0]),
                                      column_index=ih.index(field)))
        def checksum(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                             separators=(",", ":")).encode()).hexdigest()
        plans.append(dict(secid=row["secid"],begin=row["begin"],cells=cells,reasons=reasons,
                          moex_m1_reference=row.get("moex_m1_reference"), source_row_sha256=checksum(raw),
                          indicator_row_sha256=checksum(target)))
    return dict(written=False, mode="REVIEW_ONLY", source_sha256=result["source_sha256"],
                proposed_columns=missing_columns, rows=plans,
                plan_sha256=hashlib.sha256(json.dumps(
                    [result["source_sha256"], missing_columns, plans],
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                sheet_apply="REQUIRES_APPROVED_MAPPING_EXISTING_WRITER_GUARD_AUDIT_AND_READBACK")
