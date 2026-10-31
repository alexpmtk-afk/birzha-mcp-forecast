"""Offline monthly M1 OHLCV consolidation. No MOEX, Google, HOME or DB calls.

Input: verified RI-style ZIP captures, each with one source_and_derived.json
and one manifest.json. Output: one active monthly ZIP on a local filesystem.
The caller owns all external Drive/DB publication and provenance mapping.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import hashlib
import json
import math
import os
import re
import tempfile
import zipfile


MSK = timezone(timedelta(hours=3))
MAX_SOURCE_BYTES = 128_000_000
MAX_MINUTES = 60_000
IDENTITY = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")


class MonthlyM1Error(ValueError):
    """Fail-closed rather than overwrite an unverifiable M1 source."""


@dataclass(frozen=True)
class MonthlyM1Result:
    status: str
    path: str
    zip_sha256: str
    payload_sha256: str
    m1_rows: int
    volume_total: float | None
    previous_revision: str | None = None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clock(text: object) -> datetime:
    if not isinstance(text, str):
        raise MonthlyM1Error("minute timestamp must be text")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MonthlyM1Error("invalid minute timestamp") from exc
    return (dt.replace(tzinfo=MSK) if dt.tzinfo is None else dt.astimezone(MSK))


def _numeric(value: object, *, allow_none: bool = False) -> bool:
    if value is None:
        return allow_none
    return type(value) in (int, float) and math.isfinite(value)


def _read_zip_bytes(raw_zip: bytes) -> tuple[dict, dict, bytes]:
    try:
        with zipfile.ZipFile(BytesIO(raw_zip)) as z:
            names = z.namelist()
            payload_names = [n for n in names if n.endswith("source_and_derived.json")]
            manifest_names = [n for n in names if n.endswith("manifest.json")]
            if len(payload_names) != 1 or len(manifest_names) != 1:
                raise MonthlyM1Error("ambiguous payload or manifest")
            if z.getinfo(payload_names[0]).file_size > MAX_SOURCE_BYTES:
                raise MonthlyM1Error("source payload size limit")
            if z.getinfo(manifest_names[0]).file_size > 1_000_000:
                raise MonthlyM1Error("source manifest size limit")
            payload_bytes = z.read(payload_names[0])
            manifest = json.loads(z.read(manifest_names[0]))
            payload = json.loads(payload_bytes)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MonthlyM1Error("corrupt source ZIP") from exc
    if not isinstance(payload, dict) or not isinstance(manifest, dict):
        raise MonthlyM1Error("non-object source")
    expected = manifest.get("payload_sha256") or manifest.get("archive_sha256")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if expected not in (_sha(payload_bytes), _sha(canonical)):
        raise MonthlyM1Error("source payload hash mismatch")
    return payload, manifest, payload_bytes


def _month_data(source_paths: list[Path], symbol: str, secid: str, month: str,
                volume_required: bool) -> tuple[bytes, bytes, int, float | None, dict]:
    minutes: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for path in source_paths:
        raw_zip = path.read_bytes()
        zip_sha = _sha(raw_zip)
        if zip_sha in sources:
            continue
        payload, manifest, raw_bytes = _read_zip_bytes(raw_zip)
        if payload.get("exact_secid") != secid or manifest.get("secid") != secid:
            raise MonthlyM1Error("source exact SECID mismatch")
        if payload.get("symbol") != symbol:
            raise MonthlyM1Error("source symbol mismatch")
        rows = payload.get("raw", {}).get("M1")
        if not isinstance(rows, list):
            raise MonthlyM1Error("missing M1 list")
        sources[zip_sha] = {"zip_sha256": zip_sha, "payload_sha256": _sha(raw_bytes),
                            "capture_run_id": manifest.get("capture_run_id"),
                            "provider": payload.get("source", "UNKNOWN")}
        for r in rows:
            if not isinstance(r, dict):
                raise MonthlyM1Error("invalid M1 row")
            start, end = _clock(r.get("begin")), _clock(r.get("end"))
            if start.second or start.microsecond or not start <= end < start + timedelta(minutes=1):
                raise MonthlyM1Error("invalid minute boundaries")
            if start.strftime("%Y-%m") != month:
                continue  # one source capture may cross a month boundary
            if not all(_numeric(r.get(k)) for k in ("open", "high", "low", "close")):
                raise MonthlyM1Error("invalid M1 prices")
            lo, hi = r["low"], r["high"]
            if lo <= 0 or lo > min(r["open"], r["close"]) or hi < max(r["open"], r["close"]):
                raise MonthlyM1Error("inconsistent M1 OHLC")
            volume = r.get("volume")
            if not _numeric(volume, allow_none=not volume_required) or (
                volume is not None and volume < 0
            ):
                raise MonthlyM1Error("invalid M1 volume")
            row = dict(r)
            row["begin"] = start.isoformat(timespec="seconds")
            row["end"] = end.isoformat(timespec="seconds")
            key = row["begin"]
            if key in minutes and minutes[key] != row:
                raise MonthlyM1Error("conflicting M1 revisions for one SECID/minute")
            minutes[key] = row
            if len(minutes) > MAX_MINUTES:
                raise MonthlyM1Error("too many source M1 rows for one month")
    if not minutes:
        raise MonthlyM1Error("no observed M1 for requested month")
    ordered = [minutes[k] for k in sorted(minutes)]
    volumes = [r.get("volume") for r in ordered]
    total = sum(volumes) if all(v is not None for v in volumes) else None
    source_refs = [sources[k] for k in sorted(sources)]
    first, last = ordered[0]["begin"], ordered[-1]["begin"]
    doc = {"schema_version": 1, "symbol": symbol, "exact_secid": secid,
           "range": [month + "-01", date.fromisoformat(month + "-01").replace(day=1).isoformat()],
           "source": "CONSOLIDATED_FROM_VERIFIED_CAPTURE", "historical_first_seen": "UNKNOWN",
           "calendar": "NOT_INDEPENDENTLY_ATTESTED", "pit_status": "RECONSTRUCTED_NOT_PIT",
           "source_refs": source_refs, "raw": {"M1": ordered}}
    # The range's second day is set below (calendar month end).
    from calendar import monthrange
    y, m = map(int, month.split("-"))
    doc["range"][1] = f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}"
    payload = json.dumps(doc, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    payload_sha = _sha(payload)
    manifest = {"schema_version": 1, "symbol": symbol, "secid": secid, "period": month,
                "archive_sha256": payload_sha, "payload_sha256": payload_sha,
                "m1_rows": len(ordered), "m1_volume_total": total,
                "first_m1": first, "last_m1": last,
                "quality_status": "OHLCV_VALIDATED_NOT_CALENDAR_ATTESTED",
                "pit_status": "UNKNOWN", "source_refs": source_refs}
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode()
    return payload, manifest_bytes, len(ordered), total, doc


def _package(payload: bytes, manifest: bytes) -> bytes:
    result = BytesIO()
    with zipfile.ZipFile(result, "w") as z:
        for name, body in (("source_and_derived.json", payload), ("manifest.json", manifest)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, body)
    return result.getvalue()


def _atomic_write(path: Path, body: bytes) -> None:
    fd, temp = tempfile.mkstemp(prefix=".m1-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def publish_month(source_paths: list[Path], target_folder: Path, recovery_folder: Path,
                  *, symbol: str, secid: str, month: str,
                  volume_required: bool = True) -> MonthlyM1Result:
    """Publish one active monthly ZIP, retaining old revisions in recovery.

    This operates on local files only. A Google publisher must independently
    upload/read-back the bytes and record its Drive file ID and outer ZIP SHA.
    """
    if not IDENTITY.fullmatch(symbol) or not IDENTITY.fullmatch(secid):
        raise MonthlyM1Error("unsafe symbol or SECID")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise MonthlyM1Error("invalid calendar month")
    if not source_paths:
        raise MonthlyM1Error("source paths required")
    if not all(isinstance(p, Path) and p.is_file() for p in source_paths):
        raise MonthlyM1Error("source file missing")
    target_folder.mkdir(parents=True, exist_ok=True)
    target = target_folder / f"{symbol}_{secid}_{month}_SOURCE_M1_OHLCV.zip"
    recovery_folder.mkdir(parents=True, exist_ok=True)
    lock = target.with_suffix(target.suffix + ".lock")
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise MonthlyM1Error("monthly publisher busy or stale lock") from exc
    try:
        os.close(fd)
        payload, manifest, count, volume, doc = _month_data(source_paths, symbol, secid, month, volume_required)
        new_bytes = _package(payload, manifest)
        new_sha, payload_sha = _sha(new_bytes), _sha(payload)
        previous_revision = None
        if target.exists():
            old_bytes = target.read_bytes()
            _read_zip_bytes(old_bytes)  # must validate before deciding overwrite/no-op
            if old_bytes == new_bytes:
                return MonthlyM1Result("NOOP_VERIFIED", str(target), new_sha, payload_sha, count, volume)
            old_doc, _, _ = _read_zip_bytes(old_bytes)
            if old_doc.get("exact_secid") != secid or old_doc.get("symbol") != symbol:
                raise MonthlyM1Error("existing monthly target belongs to another source")
            for row in old_doc.get("raw", {}).get("M1", []):
                key = _clock(row["begin"]).isoformat(timespec="seconds")
                newer = {r["begin"]: r for r in doc["raw"]["M1"]}.get(key)
                if newer != row:
                    raise MonthlyM1Error("non-append revision needs explicit review")
            previous_revision = target.name + "." + _sha(old_bytes)[:16] + ".zip"
            old_backup = recovery_folder / previous_revision
            if old_backup.exists():
                if old_backup.read_bytes() != old_bytes:
                    raise MonthlyM1Error("existing recovery revision mismatch")
            else:
                _atomic_write(old_backup, old_bytes)
        _atomic_write(target, new_bytes)
        if target.read_bytes() != new_bytes:
            raise MonthlyM1Error("monthly readback mismatch")
        return MonthlyM1Result("PUBLISHED_READBACK_PASS", str(target), new_sha, payload_sha,
                               count, volume, previous_revision)
    finally:
        lock.unlink(missing_ok=True)
