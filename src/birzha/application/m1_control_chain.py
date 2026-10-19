"""M1 v2.2 isolated price-only control/retest *research* state machine.

A frozen, separately available anchor and level are required. This scanner
uses *claimed* candle receipts, without attesting upstream source/calendar
quality. It never sets an operational forecast ACTIVE or writes to storage.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Mapping, Sequence

from birzha.application.m1_causal_swings import CausalBar


@dataclass(frozen=True, slots=True)
class AnchorBinding:
    secid: str
    timeframe: str
    direction: str  # UP / DOWN
    event_time: datetime
    confirmed_at: datetime
    available_at: datetime
    source_ref: str
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FrozenLevel:
    level_id: str
    secid: str
    timeframe: str
    lower: float
    upper: float
    atr_frozen: float
    event_time: datetime
    confirmed_at: datetime
    available_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class ControlParameters:
    confirm_bars: int
    hold_bars: int
    reclaim_buffer_atr: float
    chain_max_age: int


def scan_m1_control_chain(
    bars: Sequence[CausalBar], *, secid: str, timeframe: str,
    t0: datetime, side: str, anchor: AnchorBinding, level: FrozenLevel,
    parameters: ControlParameters,
) -> dict[str, object]:
    """Return structural control evidence but never approve a forecast.

    Separate bars, with strictly increasing end times, must provide
    PROBE -> ACCEPTANCE -> PERSISTENCE -> RETEST -> HOLD.
    This conservative sequencing interpretation needs domain review before
    operational admission. A reclaimed / expired chain may only restart from
    a *new* probe. No stage is inferred from delta or score.
    """
    _validate_contract(secid, timeframe, t0, side, anchor, level, parameters)
    admitted, blocked = _admit_prefix(bars, secid=secid, timeframe=timeframe, t0=t0)
    common = {
        "model_id": "M1_GPT_STRUCTURE_CONTROL",
        "status": "INPUT_UNAVAILABLE" if blocked else "NO_CHAIN",
        "reason": blocked,
        "control_side": "UNCONFIRMED",
        "control_strength": "NONE",
        "causal_status": "UNAVAILABLE" if blocked else "PENDING",
        "stage": "SEARCH_PROBE",
        "level_id": level.level_id,
        "anchor_source_ref": anchor.source_ref,
        "anchor_evidence_refs": anchor.evidence_refs,
        "anchor_time": anchor.confirmed_at,
        "secid": secid, "timeframe": timeframe, "t0": t0,
        "event_times": {},
        "evidence_refs": (),
        "checked_non_reclaim_through": None,
        "last_reset_reason": None,
        "calendar_attested": False,
        "source_receipts_attested": False,
        "forecast_allowed": False,
    }
    if blocked:
        return common

    state = "SEARCH_PROBE"
    events: dict[str, dict[str, object]] = {}
    refs: list[str] = []
    starting_index: int | None = None
    persistence_count = 0
    hold_count = 0
    last_reset: str | None = None
    checked_at: datetime | None = None
    first_eligible = max(anchor.available_at, level.available_at)

    def accepted(close: float) -> bool:
        return close > level.upper if side == "BUYERS" else close < level.lower

    def reclaimed(close: float) -> bool:
        boundary = (level.lower - parameters.reclaim_buffer_atr * level.atr_frozen
                    if side == "BUYERS" else
                    level.upper + parameters.reclaim_buffer_atr * level.atr_frozen)
        return close < boundary if side == "BUYERS" else close > boundary

    def touched(bar: CausalBar) -> bool:
        return bar.low <= level.upper and bar.high >= level.lower

    def mark(stage_name: str, i: int, bar: CausalBar) -> None:
        events[stage_name] = {
            "index": i, "event_time": bar.end,
            "confirmed_at": bar.end, "available_at": bar.available_at,
            "source_ref": bar.source_ref,
        }

    def reset(reason: str) -> None:
        nonlocal state, events, refs, starting_index, persistence_count, hold_count, last_reset, checked_at
        state = "SEARCH_PROBE"
        events = {}
        refs = []
        starting_index = None
        persistence_count = 0
        hold_count = 0
        checked_at = None
        last_reset = reason

    for i, bar in enumerate(admitted):
        # The level/anchor must be *known before* the first probing bar, not
        # retrospectively created from that bar.
        if bar.start < first_eligible:
            continue

        if starting_index is not None and i - starting_index > parameters.chain_max_age:
            reset("CHAIN_EXPIRED")
            continue

        if state == "SEARCH_PROBE":
            if touched(bar) and not accepted(bar.close):
                state = "PROBED"
                starting_index = i
                mark("PROBE", i, bar)
                refs.append(bar.source_ref)
            continue

        if state == "PROBED":
            if accepted(bar.close):
                state = "PERSISTENCE_PENDING"
                mark("ACCEPTANCE", i, bar)
                refs.append(bar.source_ref)
            continue

        # Reclaim is checked on every admissible bar from ACCEPTANCE to T0,
        # including after a completed HOLD. Intrabar wick alone never reclaims.
        checked_at = bar.end
        refs.append(bar.source_ref)
        if reclaimed(bar.close):
            reset("RECLAIMED_AFTER_ACCEPTANCE")
            continue

        if state == "PERSISTENCE_PENDING":
            if not accepted(bar.close):
                reset("PERSISTENCE_INTERRUPTED")
                continue
            persistence_count += 1
            if persistence_count == parameters.confirm_bars:
                mark("PERSISTENCE", i, bar)
                state = "RETEST_PENDING"
            continue

        if state == "RETEST_PENDING":
            if touched(bar):
                if not accepted(bar.close):
                    reset("RETEST_NOT_ACCEPTED")
                    continue
                mark("RETEST", i, bar)
                state = "HOLD_PENDING"
            continue

        if state == "HOLD_PENDING":
            if not accepted(bar.close):
                reset("HOLD_INTERRUPTED")
                continue
            hold_count += 1
            if hold_count == parameters.hold_bars:
                mark("HOLD", i, bar)
                state = "RETESTED"
            continue

        # RETESTED survives only while subsequent admissible closes do not
        # reclaim and chain_max_age has not expired.

    if state == "RETESTED":
        common.update(
            status="CONFIRMED_RESEARCH_ONLY",
            control_side=side,
            control_strength="RETESTED",
            causal_status="CONFIRMED",
        )
    elif state in ("RETEST_PENDING", "HOLD_PENDING"):
        common.update(
            status="PENDING", control_side=side,
            control_strength="INITIAL", causal_status="PENDING",
        )
    elif state == "PERSISTENCE_PENDING":
        common.update(status="PENDING", control_side=side)
    elif state == "PROBED":
        common["status"] = "PENDING"

    common["stage"] = state
    common["event_times"] = events
    common["evidence_refs"] = tuple(refs)
    common["checked_non_reclaim_through"] = checked_at
    common["last_reset_reason"] = last_reset
    if state == "RETESTED":
        common["confirmed_at"] = events["HOLD"]["confirmed_at"]
        common["available_at"] = max(
            (anchor.available_at, level.available_at,
             *(e["available_at"] for e in events.values())),
        )
    return common


def _validate_contract(
    secid: str, timeframe: str, t0: datetime, side: str,
    anchor: AnchorBinding, level: FrozenLevel, p: ControlParameters,
) -> None:
    if not isinstance(secid, str) or not secid.strip() or timeframe not in ("H1", "M15"):
        raise ValueError("exact SECID and H1/M15 required")
    if not _aware(t0):
        raise ValueError("timezone-aware T0 required")
    if side not in ("BUYERS", "SELLERS"):
        raise ValueError("control side must be BUYERS or SELLERS")
    if not isinstance(anchor, AnchorBinding) or not isinstance(level, FrozenLevel):
        raise ValueError("frozen anchor and level required")
    if anchor.secid != secid or anchor.timeframe not in ("D1", "H1", "M15"):
        raise ValueError("anchor exact SECID/timeframe mismatch")
    if any(not isinstance(r, str) or not r.strip() for r in anchor.evidence_refs):
        raise ValueError("anchor: invalid chain provenance")
    if anchor.direction != ("UP" if side == "BUYERS" else "DOWN"):
        raise ValueError("anchor direction contradicts control side")
    if level.secid != secid or level.timeframe != timeframe:
        raise ValueError("level exact SECID/timeframe mismatch")
    if not isinstance(level.level_id, str) or not level.level_id.strip():
        raise ValueError("immutable level_id required")
    for name, item in (("anchor", anchor), ("level", level)):
        if not isinstance(item.source_ref, str) or not item.source_ref.strip():
            raise ValueError(f"{name}: source reference required")
        if not all(_aware(getattr(item, k)) for k in ("event_time", "confirmed_at", "available_at")):
            raise ValueError(f"{name}: timezone-aware event/confirm/available times required")
        if not item.event_time <= item.confirmed_at <= item.available_at <= t0:
            raise ValueError(f"{name}: noncausal or future evidence")
    if not all(_finite(v) for v in (level.lower, level.upper, level.atr_frozen)):
        raise ValueError("finite level bounds and ATR required")
    if level.upper < level.lower or level.atr_frozen <= 0:
        raise ValueError("ordered level bounds and positive frozen ATR required")
    if not isinstance(p, ControlParameters):
        raise ValueError("explicit control parameters required")
    if any(type(getattr(p, k)) is not int or getattr(p, k) < 1
           for k in ("confirm_bars", "hold_bars", "chain_max_age")):
        raise ValueError("positive integer control counts required")
    if not _finite(p.reclaim_buffer_atr) or p.reclaim_buffer_atr < 0:
        raise ValueError("finite nonnegative reclaim buffer required")


def _admit_prefix(
    bars: Sequence[CausalBar], *, secid: str, timeframe: str, t0: datetime,
) -> tuple[list[CausalBar], str | None]:
    admitted: list[CausalBar] = []
    previous_end: datetime | None = None
    for i, bar in enumerate(bars):
        if not isinstance(bar, CausalBar):
            raise ValueError(f"bar[{i}]: wrong type")
        if not _aware(bar.start) or not _aware(bar.end) or bar.start >= bar.end:
            raise ValueError(f"bar[{i}]: invalid timestamps")
        if bar.end > t0:
            break
        if bar.secid != secid or bar.timeframe != timeframe:
            raise ValueError(f"bar[{i}]: contract/timeframe mismatch")
        if previous_end is not None and bar.start < previous_end:
            raise ValueError(f"bar[{i}]: overlapping/out-of-order timestamps")
        previous_end = bar.end
        if not all(_finite(v) for v in (bar.open, bar.high, bar.low, bar.close)):
            raise ValueError(f"bar[{i}]: nonfinite OHLC")
        if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
            raise ValueError(f"bar[{i}]: invalid OHLC envelope")
        if type(bar.is_complete) is not bool:
            raise ValueError(f"bar[{i}]: invalid completion flag")
        if bar.available_at is not None and (
            not _aware(bar.available_at) or bar.available_at < bar.end
        ):
            raise ValueError(f"bar[{i}]: invalid available_at")
        if bar.source_ref is not None and not isinstance(bar.source_ref, str):
            raise ValueError(f"bar[{i}]: invalid source reference")
        if (not bar.is_complete or bar.available_at is None or bar.available_at > t0
                or not bar.source_ref or not bar.source_ref.strip()):
            return admitted, f"bar[{i}]: incomplete, late or missing provenance"
        admitted.append(bar)
    return admitted, None


def _aware(v: object) -> bool:
    return isinstance(v, datetime) and v.tzinfo is not None and v.utcoffset() is not None


def _finite(v: object) -> bool:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(float(v))
    except (OverflowError, TypeError, ValueError):
        return False



def bind_m1_research_anchor(result: Mapping[str, object]) -> AnchorBinding:
    """Convert a CONFIRMED anchor scanner result to a research-only binding.

    This strictly maps immutable event/provenance fields from the isolated
    upstream algorithm; it does NOT attest raw MOEX receipts or provide a
    valid H1/M15 frozen level. Level verification remains independent.
    """
    if not isinstance(result, Mapping) or result.get("model_id") != "M1_GPT_STRUCTURE_CONTROL":
        raise ValueError("M1 anchor research result required")
    if result.get("status") != "CONFIRMED_RESEARCH_ONLY" or result.get("forecast_allowed") is not False:
        raise ValueError("only non-executable confirmed research anchors accepted")
    candidate = result.get("confirmed_anchor")
    if not isinstance(candidate, Mapping):
        raise ValueError("anchor result missing confirmed candidate")
    refs = candidate.get("confirmation_source_refs")
    if (not isinstance(refs, (tuple, list)) or not refs or
            any(not isinstance(x, str) or not x.strip() for x in refs)):
        raise ValueError("anchor missing evidence chain references")
    binding = AnchorBinding(
        secid=result.get("secid"), timeframe=result.get("timeframe"),
        direction=candidate.get("direction"),
        event_time=candidate.get("event_time"),
        confirmed_at=candidate.get("confirmed_at"),
        available_at=candidate.get("available_at"),
        source_ref=refs[-1], evidence_refs=tuple(refs),
    )
    # A binding cannot pass _validate_contract without these causal facts.
    if (not isinstance(binding.secid, str) or not binding.secid.strip()
            or binding.timeframe not in ("D1", "H1", "M15")
            or binding.direction not in ("UP", "DOWN")
            or not all(_aware(t) for t in (
                binding.event_time, binding.confirmed_at, binding.available_at))
            or not binding.event_time <= binding.confirmed_at <= binding.available_at):
        raise ValueError("anchor scanner result has invalid causal fields")
    return binding
