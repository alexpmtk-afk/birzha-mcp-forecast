"""Research-only, ex-ante consolidation and breakout-anchor candidates for M1 v2.2.

No calibrated defaults, provider access, synthetic prices, trading or Forecast
integration. ATR(14) Wilder is the explicit v2.2 convention; the remaining
parameters MUST be supplied from a separately frozen candidate manifest.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Sequence

from birzha.application.m1_causal_swings import CausalBar

ATR_PERIOD = 14


@dataclass(frozen=True, slots=True)
class AnchorParameters:
    consolidation_windows: tuple[int, ...]
    er_balance: float
    range_balance_atr: float
    breakout_buffer_atr: float
    confirm_bars: int
    reclaim_buffer_atr: float


@dataclass(frozen=True, slots=True)
class Consolidation:
    start_index: int
    end_index: int
    lower: float
    upper: float
    atr_frozen: float
    er: float
    range_atr: float
    length: int
    confirmed_at: datetime
    available_at: datetime
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnchorCandidate:
    direction: str
    breakout_index: int
    event_time: datetime
    confirmed_at: datetime | None
    available_at: datetime | None
    status: str
    next_required_index: int | None
    consolidation: Consolidation
    confirmation_source_refs: tuple[str, ...]


def scan_m1_consolidation_anchors(
    bars: Sequence[CausalBar], *, secid: str, timeframe: str,
    t0: datetime, parameters: AnchorParameters,
) -> dict[str, object]:
    """Return last still-valid confirmed breakout and newest pending candidate.

    Consolidation must end at j-1, immediately before breakout bar j.
    Its boundaries and Wilder ATR are frozen *before* j. Confirm count includes
    breakout bar and requires subsequent closes outside the frozen boundaries.
    A reclaim (close through the opposite reclaim threshold) cancels that
    candidate through the available prefix. This design is research-only.
    """
    _check_parameters(parameters)
    if not _aware(t0):
        raise ValueError("T0 must be timezone-aware")
    if not secid or timeframe not in ("D1", "H1", "M15"):
        raise ValueError("exact SECID and D1/H1/M15 required")

    accepted: list[CausalBar] = []
    invalid_at_t0: str | None = None
    previous_end = None
    for i, bar in enumerate(bars):
        if not isinstance(bar, CausalBar):
            raise ValueError(f"bar[{i}]: invalid type")
        if bar.secid != secid or bar.timeframe != timeframe:
            raise ValueError(f"bar[{i}]: exact contract/timeframe mismatch")
        if not _aware(bar.start) or not _aware(bar.end) or bar.start >= bar.end:
            raise ValueError(f"bar[{i}]: invalid/naive timestamps")
        if previous_end is not None and bar.start < previous_end:
            raise ValueError(f"bar[{i}]: overlapping or unordered bars")
        previous_end = bar.end
        if not all(_finite(p) for p in (bar.open, bar.high, bar.low, bar.close)):
            raise ValueError(f"bar[{i}]: nonfinite OHLC")
        if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
            raise ValueError(f"bar[{i}]: inconsistent OHLC")
        if type(bar.is_complete) is not bool:
            raise ValueError(f"bar[{i}]: malformed completion flag")
        if bar.available_at is not None and (
            not _aware(bar.available_at) or bar.available_at < bar.end
        ):
            raise ValueError(f"bar[{i}]: malformed available_at")
        if bar.source_ref is not None and not isinstance(bar.source_ref, str):
            raise ValueError(f"bar[{i}]: malformed source reference")

        # Future bars cannot affect an older T0 even when already supplied.
        if bar.end > t0:
            break
        if (not bar.is_complete or bar.available_at is None
                or bar.available_at > t0 or not bar.source_ref
                or not bar.source_ref.strip()):
            invalid_at_t0 = f"bar[{i}]: incomplete, late or unattributed evidence"
            break
        accepted.append(bar)

    result: dict[str, object] = {
        "model_id": "M1_GPT_STRUCTURE_CONTROL",
        "status": "INPUT_UNAVAILABLE" if invalid_at_t0 else "NONE",
        "reason": invalid_at_t0,
        "t0": t0.isoformat(),
        "secid": secid, "timeframe": timeframe,
        "accepted_bars": len(accepted),
        "confirmed_anchor": None, "pending_candidate": None,
        "calendar_attested": False, "source_receipts_attested": False,
        "forecast_allowed": False,
    }
    if invalid_at_t0:
        return result

    atr = _wilder_atr14(accepted)
    confirmed: list[AnchorCandidate] = []
    pending: list[AnchorCandidate] = []
    for breakout in range(1, len(accepted)):
        prior = breakout - 1
        scale = atr[prior]
        if scale is None or scale <= 0:
            continue
        # All candidates end at the same prior bar: largest window wins.
        regions: list[Consolidation] = []
        for n in parameters.consolidation_windows:
            if prior - n < 0:  # need n closed returns, including predecessor
                continue
            start = prior - n + 1
            window = accepted[start:prior + 1]
            returns = [abs(accepted[k].close - accepted[k - 1].close)
                       for k in range(start, prior + 1)]
            total = sum(returns)
            er = abs(accepted[prior].close - accepted[start - 1].close) / total if total else 0.0
            lo = min(bar.low for bar in window)
            hi = max(bar.high for bar in window)
            width = (hi - lo) / scale
            if er <= parameters.er_balance and width <= parameters.range_balance_atr:
                regions.append(Consolidation(
                    start, prior, lo, hi, scale, er, width, n,
                    accepted[prior].end, accepted[prior].available_at,
                    tuple(bar.source_ref for bar in window),
                ))
        if not regions:
            continue
        region = max(regions, key=lambda r: r.length)
        close = accepted[breakout].close
        if close > region.upper + parameters.breakout_buffer_atr * scale:
            direction = "UP"
        elif close < region.lower - parameters.breakout_buffer_atr * scale:
            direction = "DOWN"
        else:
            continue
        candidate = _resolve_candidate(accepted, breakout, region, direction, parameters)
        if candidate.status == "CONFIRMED":
            confirmed.append(candidate)
        elif candidate.status == "PENDING":
            pending.append(candidate)

    if confirmed:
        last = confirmed[-1]
        result["status"] = "CONFIRMED_RESEARCH_ONLY"
        result["confirmed_anchor"] = asdict(last)
    elif pending:
        result["status"] = "PENDING"
    if pending:
        result["pending_candidate"] = asdict(pending[-1])
    if len(accepted) <= ATR_PERIOD:
        result["status"] = "WARMUP" if not confirmed and not pending else result["status"]
        result["reason"] = "Wilder ATR14 needs prior close plus 14 TR observations"
    return result


def _resolve_candidate(
    bars: Sequence[CausalBar], j: int, region: Consolidation,
    direction: str, p: AnchorParameters,
) -> AnchorCandidate:
    def outside(close: float) -> bool:
        return close > region.upper if direction == "UP" else close < region.lower

    def reclaimed(close: float) -> bool:
        threshold = (region.upper - p.reclaim_buffer_atr * region.atr_frozen
                     if direction == "UP" else region.lower + p.reclaim_buffer_atr * region.atr_frozen)
        return close <= threshold if direction == "UP" else close >= threshold

    confirmed_at = None
    availability = None
    proof: tuple[str, ...] = ()
    needed_end = j + p.confirm_bars - 1
    for k in range(j, len(bars)):
        close = bars[k].close
        if reclaimed(close):
            return AnchorCandidate(direction, j, bars[j].end, None, None,
                                   "RECLAIMED", None, region, ())
        if k <= needed_end and not outside(close):
            return AnchorCandidate(direction, j, bars[j].end, None, None,
                                   "ABANDONED", None, region, ())
        if k == needed_end:
            confirmed_at = bars[k].end
            availability = max(bar.available_at for bar in bars[j:k + 1])
            proof = tuple(bar.source_ref for bar in bars[j:k + 1])
    if confirmed_at is None:
        return AnchorCandidate(direction, j, bars[j].end, None, None,
                               "PENDING", needed_end, region, ())
    return AnchorCandidate(direction, j, bars[j].end, confirmed_at,
                           availability, "CONFIRMED", None, region, proof)


def _wilder_atr14(bars: Sequence[CausalBar]) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    total = 0.0
    previous = None
    for i in range(1, len(bars)):
        candle = bars[i]
        prior_close = bars[i - 1].close
        tr = max(candle.high - candle.low, abs(candle.high - prior_close),
                 abs(candle.low - prior_close))
        if i <= ATR_PERIOD:
            total += tr
            if i == ATR_PERIOD:
                previous = total / ATR_PERIOD
                out[i] = previous
        else:
            previous = (13 * previous + tr) / ATR_PERIOD
            out[i] = previous
    return out


def _check_parameters(p: AnchorParameters) -> None:
    if not isinstance(p, AnchorParameters):
        raise ValueError("complete AnchorParameters required")
    windows = p.consolidation_windows
    if not isinstance(windows, tuple) or not windows or any(
        type(n) is not int or n < 2 for n in windows
    ) or len(set(windows)) != len(windows):
        raise ValueError("explicit distinct consolidation windows >=2 required")
    if type(p.confirm_bars) is not int or p.confirm_bars < 1:
        raise ValueError("confirm_bars must be a positive integer")
    for key in ("er_balance", "range_balance_atr", "breakout_buffer_atr", "reclaim_buffer_atr"):
        value = getattr(p, key)
        if not _finite(value) or value < 0:
            raise ValueError(f"{key}: expected finite nonnegative threshold")
    if p.er_balance > 1:
        raise ValueError("er_balance must be within [0,1]")


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False
