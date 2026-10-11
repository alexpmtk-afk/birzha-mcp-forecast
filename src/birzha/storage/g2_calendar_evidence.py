"""Exact-byte bounded MOEX history witness persisted alongside canonical G2 Forecast.

Only same-provider second-endpoint corroboration; no trusted exchange calendar.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import json
from typing import Any

from birzha.application.g2_calendar_witness import verify_exact_history_concordance
from birzha.storage.g2_manifest_evidence import MAGIC


def _canonical(v: Any) -> bytes:
    return (json.dumps(v, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf8")


def encode_calendar_witness(witness: dict) -> bytes:
    """Pack all original /history HTTP response bytes and per-page receipts."""
    if not isinstance(witness, dict) or not isinstance(witness.get("pages"), list):
        raise ValueError("missing original exact SECID calendar pages")
    if not 0 < len(witness["pages"]) <= 20:
        raise ValueError("bounded calendar page count required")
    clean = {k: v for k, v in witness.items() if k != "pages"}
    clean["pages"] = []
    for page in witness["pages"]:
        if not isinstance(page, dict):
            raise ValueError("invalid calendar page")
        body = page.get("raw_body")
        if not isinstance(body, bytes) or not 0 < len(body) <= 2_000_000:
            raise ValueError("original bounded calendar page required")
        clean["pages"].append({
            **{k: v for k, v in page.items() if k != "raw_body"},
            "raw_body_b64": base64.b64encode(body).decode("ascii"),
        })
    body = _canonical(clean)
    if len(body) > 8_000_000:
        raise ValueError("calendar evidence exceeds bounded storage")
    return body


def decode_calendar_witness(body: bytes) -> dict:
    if not isinstance(body, bytes) or not 0 < len(body) <= 8_000_000:
        raise ValueError("missing stored calendar evidence")
    try:
        record = json.loads(body)
        if not isinstance(record, dict) or not isinstance(record["pages"], list):
            raise ValueError("invalid calendar shape")
        pages = []
        for p in record["pages"]:
            if not isinstance(p, dict) or "raw_body" in p:
                raise ValueError("ambiguous original calendar bytes")
            raw = base64.b64decode(p["raw_body_b64"], validate=True)
            if not 0 < len(raw) <= 2_000_000:
                raise ValueError("empty original calendar page")
            pages.append({**{k: v for k, v in p.items() if k != "raw_body_b64"}, "raw_body": raw})
        return {**{k: v for k, v in record.items() if k != "pages"}, "pages": pages}
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("malformed stored calendar witness") from exc


def source_d1_bytes(source_blob: bytes, manifest_bytes: bytes, exact_secid: str):
    """Extract originally retained D1 bytes; never accept caller-created bars."""
    manifest = json.loads(manifest_bytes)
    matches = [i for i in manifest["instruments"]
               if isinstance(i, dict) and i.get("resolved_secid") == exact_secid
               and i.get("status") == "SOURCE_CAPTURE_COMPLETE"]
    if len(matches) != 1:
        raise ValueError("one exact security required")
    ins = matches[0]["instrument"]
    names = tuple(p["path"] for p in matches[0]["timeframes"]["D1"]["pages"])
    if not names or not source_blob.startswith(MAGIC):
        raise ValueError("unbound original D1 pages")
    all_pages, pos = {}, len(MAGIC)
    while pos < len(source_blob):
        if pos + 4 > len(source_blob):
            raise ValueError("truncated source page header")
        n = int.from_bytes(source_blob[pos:pos + 4], "big")
        pos += 4
        if not 0 < n <= 255 or pos + n + 8 > len(source_blob):
            raise ValueError("unsafe source page filename")
        name = source_blob[pos:pos + n].decode("utf8")
        pos += n
        length = int.from_bytes(source_blob[pos:pos + 8], "big")
        pos += 8
        if not 0 < length <= 16_000_000 or pos + length > len(source_blob) or name in all_pages:
            raise ValueError("unsafe source page contents")
        all_pages[name] = source_blob[pos:pos + length]
        pos += length
    if any(n not in all_pages for n in names):
        raise ValueError("missing original D1")
    return ins, tuple(all_pages[n] for n in names)


def verify_calendar_evidence(
    body: bytes, *, source_blob: bytes, manifest_bytes: bytes,
    exact_secid: str, issued_at_t0: str,
) -> dict:
    from birzha.application.g2_calendar_witness import CalendarWitnessRefused

    try:
        witness = decode_calendar_witness(body)
        ins, original_d1 = source_d1_bytes(source_blob, manifest_bytes, exact_secid)
        return verify_exact_history_concordance(
            instrument=ins, d1_raw_pages=original_d1,
            witness=witness, issue_t0=issued_at_t0,
        )
    except (KeyError, ValueError, TypeError, IndexError, UnicodeError) as exc:
        raise ValueError("calendar witness not bound to original Forecast source") from exc
