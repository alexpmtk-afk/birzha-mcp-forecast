"""Bounded ex-ante OHLC pivot facts for M1 v2.2 (research-only).

The only selected number is explicit swing_w. No hidden calibration, provider,
calendar, forecasting, persistence or MCP access. A candle high/low's *tick
time* is unknown; its event interval (bar start, end) is retained, and
`event_time` uses a clearly labeled BAR_END_PROXY coordinate.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Sequence


@dataclass(frozen=True, slots=True)
class CausalBar:
    secid: str
    timeframe: str
    start: datetime
    end: datetime
    available_at: datetime | None
    open: float
    high: float
    low: float
    close: float
    is_complete: bool
    source_ref: str | None


@dataclass(frozen=True, slots=True)
class ConfirmedSwing:
    side: str  # HIGH | LOW
    secid: str
    timeframe: str
    center_index: int
    price: float
    event_start_time: datetime
    event_end_time: datetime
    event_time: datetime  # BAR_END_PROXY, not exact time of extreme
    event_time_precision: str
    confirmed_at: datetime
    available_at: datetime
    window_start: datetime
    window_end: datetime
    swing_w: int
    tie_break: str
    source_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def scan_m1_confirmed_swings(
    bars: Sequence[CausalBar],
    *,
    secid: str,
    timeframe: str,
    t0: datetime,
    swing_w: int,
    tie_break: str,
) -> dict[str, object]:
    """Identify local pivots only after an entire causal right window.

    A missing/late/incomplete bar blocks its *whole window*: removing it and
    compressing neighboring candles would create a false pivot. The resulting
    extrema are structural candidates, not Control or a tradable scenario.
    """
    if not _aware(t0):
        raise ValueError("t0 must be timezone-aware")
    if type(swing_w) is not int or swing_w < 1:
        raise ValueError("swing_w must be a positive integer, frozen before scanning")
    if tie_break not in ("EARLIEST", "LATEST"):
        raise ValueError("tie_break must explicitly be EARLIEST or LATEST")
    if not isinstance(secid, str) or not secid.strip():
        raise ValueError("exact SECID required")
    if timeframe not in ("D1", "H1", "M15"):
        raise ValueError("supported timeframe required")

    previous_end: datetime | None = None
    for n, bar in enumerate(bars):
        if not isinstance(bar, CausalBar):
            raise ValueError(f"invalid bar type at index {n}")
        if bar.secid != secid or bar.timeframe != timeframe:
            raise ValueError(f"contract/timeframe mismatch at index {n}")
        if not _aware(bar.start) or not _aware(bar.end) or not bar.start < bar.end:
            raise ValueError(f"naive or invalid bar interval at index {n}")
        if previous_end is not None and bar.start < previous_end:
            raise ValueError(f"out-of-order or overlapping bars at index {n}")
        previous_end = bar.end
        if bar.available_at is not None and (
            not _aware(bar.available_at) or bar.available_at < bar.end
        ):
            raise ValueError(f"invalid source availability time at index {n}")
        if not all(_finite(value) for value in (bar.open, bar.high, bar.low, bar.close)):
            raise ValueError(f"nonfinite OHLC at index {n}")
        if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close) or bar.low > bar.high:
            raise ValueError(f"invalid OHLC envelope at index {n}")

    confirmed: list[ConfirmedSwing] = []
    blocked_windows = 0
    full_windows = max(0, len(bars) - 2 * swing_w)
    for center in range(swing_w, len(bars) - swing_w):
        window = bars[center - swing_w : center + swing_w + 1]
        if any(
            not bar.is_complete
            or bar.end > t0
            or bar.available_at is None
            or bar.available_at > t0
            or not bar.source_ref
            for bar in window
        ):
            blocked_windows += 1
            continue

        # This is a deterministic tie choice on *existing* bars only.
        highs = [bar.high for bar in window]
        lows = [bar.low for bar in window]
        if max(highs) <= min(lows):  # entirely flat range: no directional extrema
            continue
        for side, prices, pivot_price in (
            ("HIGH", highs, max(highs)),
            ("LOW", lows, min(lows)),
        ):
            matches = [idx for idx, value in enumerate(prices) if value == pivot_price]
            selected = matches[0] if tie_break == "EARLIEST" else matches[-1]
            if selected != swing_w:
                continue
            bar = bars[center]
            available_at = max(item.available_at for item in window if item.available_at is not None)
            confirmed.append(ConfirmedSwing(
                side=side, secid=secid, timeframe=timeframe, center_index=center,
                price=pivot_price, event_start_time=bar.start, event_end_time=bar.end,
                event_time=bar.end, event_time_precision="BAR_END_PROXY",
                confirmed_at=window[-1].end, available_at=available_at,
                window_start=window[0].start, window_end=window[-1].end,
                swing_w=swing_w, tie_break=tie_break,
                source_refs=tuple(item.source_ref for item in window if item.source_ref),
            ))
    return {
        "model_id": "M1_GPT_STRUCTURE_CONTROL",
        "status": "RESEARCH_CANDIDATES_ONLY",
        "secid": secid,
        "timeframe": timeframe,
        "t0": t0.isoformat(),
        "swings": [s.to_dict() for s in confirmed],
        "total_complete_windows": full_windows - blocked_windows,
        "blocked_windows": blocked_windows,
        "calendar_attested": False,
        "source_receipts_attested": False,
        "forecast_allowed": False,
    }


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (ValueError, TypeError, OverflowError):
        return False
