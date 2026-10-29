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


def _require_available(data: Mapping[str, object], prev: str, secid: str,
                       cutoff: datetime) -> None:
    """Require real causal metadata; a guessed publication time never passes."""
    if data.get("is_complete") is not True:
        raise EvidenceError(f"Unfinished prior D1: {prev}/{secid}")
    if data.get("available_at_confidence") != "EXACT":
        raise EvidenceError(f"Historical availability unverified: {prev}/{secid}")
    publication = data.get("published_at")
    if publication is None:
        raise EvidenceError(f"Publication time unknown: {prev}/{secid}")
    if _parse_datetime(str(publication)) > cutoff:
        raise EvidenceError(f"Source not yet available at T0: {prev}/{secid}")


def decision_ledger(
    trading_sessions: Sequence[str],
    d1: Mapping[str, Mapping[str, Mapping[str, object]]],
    expiries: Mapping[str, str],
    *,
    decision_cutoffs: Mapping[str, str] | None = None,
    strict_pit: bool = True,
) -> tuple[list[dict], list[Roll]]:
    """Decide target contract using ONLY prior-session rows, never target OHLCV.

    Caller provides exchange trade-session identity, validated exact contract
    universe and recorded availability evidence. Results are a plan, NOT
    target-day observations or forecast-ready history.
    """
    if list(trading_sessions) != sorted(set(trading_sessions)):
        raise EvidenceError("Sessions must be unique and increasing")
    if strict_pit and not decision_cutoffs:
        raise EvidenceError("Missing historical decision cutoffs")
    plans: list[dict] = []
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
                _require_available(data, prev, secid, cutoff)
            volume = data.get("volume")
            if not isinstance(volume, (int, float)) or volume < 0:
                raise EvidenceError(f"Invalid volume: {prev}/{secid}")
            if volume > 0:
                eligible.append((secid, volume))
        if not eligible:
            raise EvidenceError(f"No eligible contract with positive T-1 volume for {target}")
        selected = sorted(eligible, key=lambda x: (-x[1], x[0]))[0][0]
        plans.append({"effective_session": target, "decision_session": prev,
                      "secid": selected, "decision_volume": prior[selected]["volume"],
                      "decision_cutoff": decision_cutoffs[target] if strict_pit else None,
                      "strict_source_PIT": strict_pit})
        if len(plans) < 2 or plans[-2]["secid"] == selected:
            continue
        old = plans[-2]["secid"]
        old_d = prior.get(old)
        new_d = prior.get(selected)
        if old_d is None or new_d is None:
            raise EvidenceError("Switch requires old/new D1 overlap at decision session")
        if strict_pit:
            # Expiring old contract can be ineligible for target, yet its close
            # still contributes to basis: its availability MUST be verified.
            _require_available(old_d, prev, old, cutoff)
        old_close = float(old_d["close"])
        new_close = float(new_d["close"])
        if old_close <= 0 or new_close <= 0:
            raise EvidenceError("Invalid switch close")
        if any(e.old_secid == selected and e.new_secid == old for e in events):
            raise EvidenceError("Unstable contract flip-flop")
        events.append(Roll(prev, target, old, selected,
                           new_close - old_close, new_close / old_close))
    return plans, events


def schedule(
    trading_sessions: Sequence[str],
    d1: Mapping[str, Mapping[str, Mapping[str, object]]],
    expiries: Mapping[str, str],
    *,
    decision_cutoffs: Mapping[str, str] | None = None,
    strict_pit: bool = True,
) -> tuple[list[dict], list[Roll]]:
    """Join ex-ante decisions to ex-post observed target bars for RESEARCH.

    This routine is not a forecast T0 decision API: target-day bar existence
    and OHLCV are accessed only AFTER decision_ledger() is finished.
    """
    plans, events = decision_ledger(
        trading_sessions, d1, expiries,
        decision_cutoffs=decision_cutoffs, strict_pit=strict_pit
    )
    chosen = []
    for plan in plans:
        target = plan["effective_session"]
        secid = plan["secid"]
        if secid not in d1.get(target, {}):
            raise EvidenceError(f"Assigned contract has no D1 on {target}: {secid}")
        chosen.append({"date": target, "decision_session": plan["decision_session"],
                       **dict(d1[target][secid]), "secid": secid})
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
