"""Immutable, generation-scoped evidence for exact D1 contract warmup dates."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from birzha.storage.historical_store import HistoricalCandleStore


WARMUP_SESSION_EVIDENCE_CONTRACT = "CONTRACT_WARMUP_V2_ACTIVITY"


class WarmupSessionEvidenceOrigin(str, Enum):
    """How a generation of expected D1 dates was obtained."""

    CAPTURED_AT_SYNC = "CAPTURED_AT_SYNC"
    RECONSTRUCTED_MOEX = "RECONSTRUCTED_MOEX"


def canonical_expected_d1_dates(expected_dates: Sequence[date]) -> tuple[date, ...]:
    """Return a sorted, unique D1 date sequence; reject ambiguous inputs."""
    dates = tuple(expected_dates)
    if not dates:
        raise ValueError("expected D1 dates must not be empty")
    if any(not isinstance(item, date) or isinstance(item, datetime) for item in dates):
        raise ValueError("expected D1 dates must contain date values, not datetimes")
    if len(set(dates)) != len(dates):
        raise ValueError("expected D1 dates must be unique")
    return tuple(sorted(dates))


def _validate_secid(secid: str) -> None:
    if not secid or secid != secid.strip() or "#" in secid:
        raise ValueError("SECID must be non-empty, trimmed, and contain no '#' delimiter")


def warmup_session_generation_id(
    *,
    secid: str,
    expected_dates: Sequence[date],
    origin: WarmupSessionEvidenceOrigin,
) -> str:
    """Fingerprint the contract, origin, evidence contract, and canonical date list."""
    _validate_secid(secid)
    if not isinstance(origin, WarmupSessionEvidenceOrigin):
        raise ValueError("origin must be a WarmupSessionEvidenceOrigin")
    canonical_dates = canonical_expected_d1_dates(expected_dates)
    payload = json.dumps(
        {
            "contract": WARMUP_SESSION_EVIDENCE_CONTRACT,
            "date_kind": "D1_EXPECTED",
            "origin": origin.value,
            "secid": secid,
            "expected_dates": [item.isoformat() for item in canonical_dates],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def warmup_session_evidence_key(
    *,
    secid: str,
    expected_dates: Sequence[date],
    origin: WarmupSessionEvidenceOrigin,
) -> str:
    """Build an immutable exact-symbol key for one expected-date generation."""
    generation_id = warmup_session_generation_id(
        secid=secid,
        expected_dates=expected_dates,
        origin=origin,
    )
    return (
        f"{secid}#{WARMUP_SESSION_EVIDENCE_CONTRACT}#D1_EXPECTED#"
        f"{origin.value}#{generation_id}"
    )


def read_verified_warmup_session_dates(
    store: HistoricalCandleStore,
    *,
    evidence_key: str,
    secid: str,
    from_date: str,
    till_date: str,
) -> tuple[date, ...]:
    """Read a complete exact generation; the range must span every stored date."""
    _validate_secid(secid)
    parts = evidence_key.split("#")
    if (
        len(parts) != 5
        or parts[0] != secid
        or parts[1] != WARMUP_SESSION_EVIDENCE_CONTRACT
        or parts[2] != "D1_EXPECTED"
        or parts[3] not in {item.value for item in WarmupSessionEvidenceOrigin}
        or len(parts[4]) != 64
        or any(character not in "0123456789abcdef" for character in parts[4])
    ):
        raise ValueError("evidence_key is not a valid exact-SECID D1 warmup generation")
    if not store.is_session_range_verified(evidence_key, from_date, till_date):
        raise RuntimeError("warmup session evidence range is not verified")
    rows = store.stored_session_contracts(evidence_key, from_date, till_date)
    if not rows:
        raise RuntimeError("verified warmup session evidence contains no dates")
    if any(row_secid != secid for _, row_secid in rows):
        raise RuntimeError("warmup session evidence contains a different SECID")
    date_values = tuple(item for item, _ in rows)
    if len(set(date_values)) != len(date_values):
        raise RuntimeError("warmup session evidence contains duplicate dates")
    try:
        parsed_dates = tuple(date.fromisoformat(item[:10]) for item in date_values)
        origin = WarmupSessionEvidenceOrigin(parts[3])
        expected_key = warmup_session_evidence_key(
            secid=secid,
            expected_dates=parsed_dates,
            origin=origin,
        )
    except ValueError as exc:
        raise RuntimeError("warmup session evidence contains an invalid date") from exc
    if expected_key != evidence_key:
        raise RuntimeError("warmup session evidence dates do not match generation fingerprint")
    return parsed_dates
