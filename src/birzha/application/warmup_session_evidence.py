"""Immutable exact-session keys for reconstructed warmup evidence.

The key fingerprints *dates*, not historical first-seen prices or exchange
revisions. CAPTURED_AT_SYNC means captured by the current sync invocation;
it is never a claim that the calendar was known at an old forecast T0.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal


WarmupEvidenceOrigin = Literal["CAPTURED_AT_SYNC", "RECONSTRUCTED_MOEX"]
WARMUP_VERIFICATION_VERSION = "CONTRACT_WARMUP_V2_ACTIVITY"


def warmup_d1_evidence_key(
    secid: str,
    expected_dates: tuple[str, ...],
    *,
    origin: WarmupEvidenceOrigin = "CAPTURED_AT_SYNC",
) -> str:
    """Content-addressed namespace; equal calendars are idempotent.

    Each exact SECID/date list has its own key. Old range-only evidence
    remains under the legacy key and is not upgraded implicitly.
    """
    if not secid or "#" in secid:
        raise ValueError("exact SECID must be non-empty without #")
    if origin not in ("CAPTURED_AT_SYNC", "RECONSTRUCTED_MOEX"):
        raise ValueError("unknown warmup evidence origin")
    if not expected_dates:
        raise ValueError("warmup evidence needs at least one expected date")
    for day in expected_dates:
        try:
            canonical = date.fromisoformat(day).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError("warmup dates must be ISO D1 trade dates") from exc
        if day != canonical:
            raise ValueError("warmup dates must use canonical YYYY-MM-DD")
    if len(expected_dates) != len(set(expected_dates)):
        raise ValueError("duplicate warmup dates are not valid evidence")
    canonical_dates = tuple(sorted(expected_dates))
    payload = json.dumps(
        {
            "secid": secid,
            "timeframe": "D1",
            "version": WARMUP_VERIFICATION_VERSION,
            "origin": origin,
            "dates": canonical_dates,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return (
        f"{secid}#{WARMUP_VERIFICATION_VERSION}"
        f"#D1_EXPECTED#{origin}#{fingerprint}"
    )
