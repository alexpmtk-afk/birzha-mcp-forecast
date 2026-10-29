"""Pure, isolated rollover reference; NOT connected to runtime or production.

Input times must already be mapped to official MOEX TRADE SESSIONS.
Point-in-time is accepted only when every eligible candidate has verifiable
published_at <= decision_cutoff for that target trading session.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence


class EvidenceError(ValueError):
    """Fail closed if history cannot support a causal switch."""


@dataclass(frozen=True)
class Roll:
    decision_session: str
    effective_session: str
    old_secid: str
    new_secid: str
    basis: float
    ratio: float


def _parse_datetime(s: str) -> datetime:
    value = datetime.fromisoformat(s)
    if value.tzinfo is None:
        raise EvidenceError("Time must include an explicit offset")
    return value


def schedule(
    trading_sessions: Sequence[str],
    d1: Mapping[str, Mapping[str, Mapping[str, object]]],
    expiries: Mapping[str, str],
    *,
    decision_cutoffs: Mapping[str, str] | None = None,
    strict_pit: bool = True,
) -> tuple[list[dict], list[Roll]]:
    """Pick one exact contract per D1 trade session from previously completed D1.

    Research-only relaxed mode does not establish historic source availability.
    d1[trade_session][secid] = {open,high,low,close,volume,published_at?}.
    """
    if list(trading_sessions) != sorted(set(trading_sessions)):
        raise EvidenceError("Sessions must be unique and increasing")
    if strict_pit and not decision_cutoffs:
        raise EvidenceError("Missing historical decision cutoffs")
    chosen: list[dict] = []
    events: list[Roll] = []
    for prev, target in zip(trading_sessions, trading_sessions[1:]):
        prior = d1.get(prev)
        if not prior:
            raise EvidenceError(f"No D1 evidence from previous session {prev}")
        cutoff = None
        if strict_pit:
            if target not in decision_cutoffs:
                raise EvidenceError(f"No decision cutoff for {target}")
            cutoff = _parse_datetime(decision_cutoffs[target])
        eligible = []
        for secid, data in prior.items():
            if secid not in expiries or expiries[secid] < target:
                continue
            if strict_pit:
                publication = data.get("published_at")
                if publication is None:
                    raise EvidenceError(f"Publication time unknown: {prev}/{secid}")
                if _parse_datetime(str(publication)) > cutoff:
                    raise EvidenceError(f"Source not yet available at T0: {prev}/{secid}")
            volume = data.get("volume")
            if not isinstance(volume, (int, float)) or volume < 0:
                raise EvidenceError(f"Invalid volume: {prev}/{secid}")
            if volume > 0:
                eligible.append((secid, volume))
        if not eligible:
            raise EvidenceError(f"No eligible contract with positive T-1 volume for {target}")
        selected = sorted(eligible, key=lambda x: (-x[1], x[0]))[0][0]
        if selected not in d1.get(target, {}):
            raise EvidenceError(f"Assigned contract has no D1 on {target}: {selected}")
        new_row = dict(d1[target][selected])
        chosen.append({"date": target, "decision_session": prev,
                       "secid": selected, **new_row})
        if len(chosen) < 2 or chosen[-2]["secid"] == selected:
            continue
        old = chosen[-2]["secid"]
        old_d = prior.get(old)
        new_d = prior.get(selected)
        if old_d is None or new_d is None:
            raise EvidenceError("Switch requires old/new D1 overlap at decision session")
        old_close = float(old_d["close"])
        new_close = float(new_d["close"])
        if old_close <= 0 or new_close <= 0:
            raise EvidenceError("Invalid switch close")
        if any(e.old_secid == selected and e.new_secid == old for e in events):
            raise EvidenceError("Unstable contract flip-flop")
        events.append(Roll(prev, target, old, selected,
                           new_close - old_close, new_close / old_close))
    return chosen, events


def coordinates(chosen: Sequence[dict], events: Sequence[Roll], *,
                analysis_as_of: str) -> list[dict]:
    """Build three coordinates from one ledger; do not alter source or volume."""
    out = []
    for row in chosen:
        t = row["date"]
        if t > analysis_as_of:
            continue
        past_basis = sum(e.basis for e in events if e.effective_session <= t)
        future_basis = sum(e.basis for e in events if t < e.effective_session <= analysis_as_of)
        factor = 1.0
        for e in events:
            if t < e.effective_session <= analysis_as_of:
                factor *= e.ratio
        out.append({
            "date": t, "secid": row["secid"],
            "RAW_close": float(row["close"]),
            "FORWARD_close": float(row["close"]) - past_basis,
            "BACK_close": float(row["close"]) + future_basis,
            "RATIO_close": float(row["close"]) * factor,
            "RAW_volume": row["volume"],
            "basis_version_as_of": analysis_as_of,
        })
    return out
