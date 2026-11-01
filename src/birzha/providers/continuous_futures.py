"""One fail-closed, exact-contract T-1 futures selection rule for MOEX.

Historical ISS market results have no attested *first publication* timestamps.
The selection is a RECONSTRUCTED ex-ante T-1 decision, NOT strict PIT proof.
No trading or storage writes. All source prices remain exact-contract RAW.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
import re
from typing import Any, Mapping, Sequence

# Reject futures calendars/spreads/options; use one outright monthly contract.
# Root/asset identity comes from authoritative ASSETCODE, not this SECID shape.
_OUTRIGHT = re.compile(r"^[A-Za-z]{1,8}[FGHJKMNQUVXZ]\d{1,2}$", re.ASCII)


def moex_asset_code(root: str) -> str:
    """Public RI SECID family is reported as ASSETCODE=RTS by ISS."""
    return "RTS" if root.strip().upper() == "RI" else root.strip()


def asset_matches_root(root: str, asset: str) -> bool:
    # Keep existing explicitly labelled RI research rows readable as well.
    return asset.casefold() in {root.strip().casefold(), moex_asset_code(root).casefold()}


class ContinuousFuturesEvidenceError(ValueError):
    """Insufficient or inconsistent source evidence; no silent fallback."""


@dataclass(frozen=True)
class CausalContractDecision:
    root: str
    decision_session: date
    effective_session: date
    secid: str
    volume: float
    roll_policy: str = "MOEX_T_MINUS_1_VOLUME_EXPIRY_V1"
    availability_confidence: str = "RECONSTRUCTED_NOT_PIT_VERIFIED"


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        value = row.get(key.lower())
    return str(value).strip() if value is not None else ""


def select_completed_session_volume(
    root: str,
    *,
    decision_session: date,
    effective_session: date,
    decision_rows: Sequence[Mapping[str, Any]],
) -> CausalContractDecision:
    """Choose the one trading outright with largest *prior* completed D1 volume.

    All evidence must belong to the same decision trade session. Require a
    listed expiry date and reject any contract already expired on target date.
    Tie-break on SECID lexicographically. Never inspect the target day's rows.
    """
    if not root.strip() or decision_session >= effective_session:
        raise ContinuousFuturesEvidenceError("Invalid root/session decision chronology")
    best: list[tuple[float, str]] = []
    for row in decision_rows:
        trade_date = _text(row, "TRADEDATE")
        if not trade_date:
            raise ContinuousFuturesEvidenceError("Missing source TRADEDATE")
        if trade_date[:10] != decision_session.isoformat():
            raise ContinuousFuturesEvidenceError("Cross-session D1 history in decision rows")
        secid = _text(row, "SECID")
        asset = _text(row, "ASSETCODE")
        if not secid or not asset or not asset_matches_root(root, asset):
            continue
        if not _OUTRIGHT.fullmatch(secid):
            continue
        expiry = _text(row, "LASTTRADEDATE")[:10]
        try:
            expiry_date = date.fromisoformat(expiry)
        except ValueError as exc:
            raise ContinuousFuturesEvidenceError("Missing/invalid outright expiry") from exc
        if expiry_date < effective_session:
            continue
        raw_volume = row.get("VOLUME", row.get("volume"))
        if raw_volume is None:
            trades = row.get("NUMTRADES", row.get("numtrades"))
            if type(trades) in (int, float) and trades == 0:
                # Official zero trades proves inactivity; retain NULL in RAW.
                continue
            raise ContinuousFuturesEvidenceError("Unverifiable candidate D1 volume")
        try:
            volume = float(raw_volume)
        except (ValueError, TypeError) as exc:
            raise ContinuousFuturesEvidenceError("Invalid D1 volume") from exc
        if not math.isfinite(volume) or volume < 0:
            raise ContinuousFuturesEvidenceError("Invalid or negative D1 volume")
        if volume > 0:
            best.append((volume, secid))
    if not best:
        raise ContinuousFuturesEvidenceError(
            f"No eligible verified-volume outright for {root} on {effective_session}"
        )
    volume, secid = sorted(best, key=lambda x: (-x[0], x[1]))[0]
    return CausalContractDecision(root, decision_session, effective_session, secid, volume)
