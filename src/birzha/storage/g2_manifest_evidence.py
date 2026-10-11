"""Read back an ORIGINAL governed MOEX manifest from canonical DuckDB evidence.

Verify raw bytes of every contributing page against the stored source bundle.
This is internal SHA integrity, never independent provider/time attestation.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import json
from pathlib import Path


MAGIC = b"G2_RAW_MULTI_FRAME_SOURCE_BUNDLE_V1\n"


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def verified_original_manifest(receipt: dict, original_bundle: bytes) -> bytes | None:
    """Return exact original JSON bytes, or None for legacy unbacked captures."""
    encoded = receipt.get("original_source_manifest_b64")
    promised = receipt.get("original_source_manifest_sha256")
    if encoded is None and promised is None:
        return None
    if not isinstance(encoded, str) or not isinstance(promised, str):
        raise ValueError("incomplete original source manifest reference")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if _digest(raw) != promised:
            raise ValueError("original source manifest digest mismatch")
        manifest = json.loads(raw)
        if (not isinstance(manifest, dict)
            or manifest.get("schema") != "G2_SIX_MARKET_LIVE_SOURCE_RECEIPT_V1"
            or manifest.get("origin") != "REAL_MOEX_PUBLIC_ISS_CAPTURE"):
            raise ValueError("incorrect original manifest origin")
        entries = manifest.get("instruments")
        if not isinstance(entries, list):
            raise ValueError("missing original instrument inventory")
        matches = [e for e in entries if isinstance(e, dict)
                   and e.get("resolved_secid") == receipt.get("secid")
                   and e.get("status") == "SOURCE_CAPTURE_COMPLETE"]
        if len(matches) != 1:
            raise ValueError("ambiguous original exact SECID")
        meta = matches[0]
        frames = meta.get("timeframes")
        if not isinstance(frames, dict) or set(frames) != {"D1", "H1", "M1"}:
            raise ValueError("original timeframes differ")
        listed = {}
        for entry in manifest.get("all_payload_files", []):
            name, digest = entry["name"], entry["sha256"]
            if (not isinstance(name, str) or Path(name).name != name
                or name in listed or not isinstance(digest, str)):
                raise ValueError("invalid original page inventory")
            listed[name] = digest
        expected = {}
        for tf in ("D1", "H1", "M1"):
            if frames[tf].get("exact_secid") != receipt["secid"]:
                raise ValueError("source page SECID does not match Forecast")
            for p in frames[tf]["pages"]:
                name, digest = p["path"], p["sha256"]
                if name in expected or listed.get(name) != digest:
                    raise ValueError("unbound original page digest")
                if not p.get("observed_start_utc") or not p.get("observed_end_utc"):
                    raise ValueError("original page receipt timestamp absent")
                expected[name] = digest
        if not expected:
            raise ValueError("no original source pages")
        if not original_bundle.startswith(MAGIC):
            raise ValueError("wrong original source bundle")
        position, found = len(MAGIC), {}
        while position < len(original_bundle):
            if position + 4 > len(original_bundle):
                raise ValueError("truncated original bundle marker")
            name_size = int.from_bytes(original_bundle[position:position+4], "big")
            position += 4
            if name_size < 1 or name_size > 255 or position+name_size+8 > len(original_bundle):
                raise ValueError("invalid original bundle marker")
            name = original_bundle[position:position+name_size].decode("utf-8")
            position += name_size
            content_size = int.from_bytes(original_bundle[position:position+8], "big")
            position += 8
            if (content_size < 1 or content_size > 16_000_000
                or position+content_size > len(original_bundle)
                or Path(name).name != name or name in found):
                raise ValueError("invalid original bundle content")
            body = original_bundle[position:position+content_size]
            position += content_size
            found[name] = _digest(body)
        if found != expected:
            raise ValueError("original manifest page SHA does not match stored received bytes")
        return raw
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError) as exc:
        raise ValueError("original source manifest integrity invalid") from exc
