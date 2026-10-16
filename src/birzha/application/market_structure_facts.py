"""Descriptive, point-in-time *research* facts for Protocol 08 Market State.

No regime score, BUY/SELL signal, barrier, entry or trade recommendation.
Use on one exact SECID/timeframe. Output statuses are not imputed to zero.
Historical reconstruction is NOT proven T0 availability.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import median

from birzha.domain.market import Candle, CandleSeries

VERSION = "MARKET_STRUCTURE_FACTS_V2_20261017"
ARCHIVED_SOURCE = "G2_DURATION_NORMALIZED_ARCHIVE_RESEARCH_ONLY"
MODES = {"RECONSTRUCTED_RESEARCH_ONLY", "T0_METADATA_GATED"}
WINDOW = 50
SWING_LEFT = SWING_RIGHT = 2
ATR_PERIOD = 14
VOLUME_BASELINE = 20


def _dt(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be explicit")
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO timestamp") from exc
    if out.tzinfo is None or out.utcoffset() is None:
        raise ValueError("timezone-aware timestamps required")
    return out.astimezone(timezone.utc)


def _numeric(value: object, *, positive: bool = True) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and (
        value > 0 if positive else value >= 0
    )


def _fact(status: str, value=None, reason: str | None = None) -> dict:
    return {"status": status, "value": value, "reason": reason}


def _wilder(rows: tuple[Candle, ...]) -> dict:
    if len(rows) < WINDOW:
        return _fact("INSUFFICIENT_HISTORY", reason=f"need_{WINDOW}_completed_bars")
    recent = rows[-WINDOW:]
    if any(not all(_numeric(v) for v in (c.high, c.low, c.close)) for c in recent):
        return _fact("UNAVAILABLE", reason="missing_OHLC_inside_fixed_window")
    ranges = [
        max(cur.high-cur.low, abs(cur.high-prev.close), abs(cur.low-prev.close))
        for prev, cur in zip(recent[:-1], recent[1:])
    ]
    if len(ranges) < ATR_PERIOD + 5:
        return _fact("INSUFFICIENT_HISTORY", reason="wilder_warmup")
    atrs = [sum(ranges[:ATR_PERIOD])/ATR_PERIOD]
    for tr in ranges[ATR_PERIOD:]:
        atrs.append(((ATR_PERIOD-1)*atrs[-1] + tr)/ATR_PERIOD)
    current, older = atrs[-1], atrs[-6]
    return _fact("AVAILABLE", {
        "atr_price": round(current, 8),
        "atr_pct": round((current/recent[-1].close) * 100, 6),
        "atr_ratio": round(current/recent[-1].close, 8),
        "atr_pct_unit": "PERCENT_100X_RATIO",
        "change_from_5_bars_ago_pct": round((current/older-1)*100, 6)
        if older > 0 else None,
        "formula": "WILDER_TR14_SEEDED_WITH_SMA14_FIXED_LAST_50",
    })


def _swings(rows: tuple[Candle, ...]) -> dict:
    if len(rows) < WINDOW:
        return _fact("INSUFFICIENT_HISTORY", reason=f"need_{WINDOW}_completed_bars")
    recent = rows[-WINDOW:]
    if any(not _numeric(c.high) or not _numeric(c.low) for c in recent):
        return _fact("UNAVAILABLE", reason="missing_high_low_inside_fixed_window")
    highs, lows = [], []
    for index in range(SWING_LEFT, WINDOW-SWING_RIGHT):
        current = recent[index]
        surrounding = [
            recent[j] for j in range(index-SWING_LEFT, index+SWING_RIGHT+1)
            if j != index
        ]
        # The pivot exists only after TWO later *completed T0-visible* bars.
        if all(current.high > bar.high for bar in surrounding):
            highs.append((current.end, current.high))
        if all(current.low < bar.low for bar in surrounding):
            lows.append((current.end, current.low))
    if len(highs) < 2 or len(lows) < 2:
        return _fact("INSUFFICIENT_HISTORY", reason="need_two_confirmed_highs_and_lows")
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    structure = "HH_HL" if hh and hl else "LH_LL" if lh and ll else "MIXED"
    return _fact("AVAILABLE", {
        "structure": structure,
        "last_two_highs": [{"pivot_end": end, "price": price} for end,price in highs[-2:]],
        "last_two_lows": [{"pivot_end": end, "price": price} for end,price in lows[-2:]],
        "confirmation_bars_right": SWING_RIGHT,
        "strict_ties_are_not_pivots": True,
    })


def _relative_volume(rows: tuple[Candle, ...], supports_volume: bool) -> dict:
    if not supports_volume:
        return _fact("NOT_APPLICABLE", reason="instrument_does_not_support_volume")
    if len(rows) < VOLUME_BASELINE + 1:
        return _fact("INSUFFICIENT_HISTORY", reason="need_21_completed_bars")
    window = rows[-(VOLUME_BASELINE+1):]
    if any(not _numeric(c.volume, positive=False) for c in window):
        return _fact("UNAVAILABLE", reason="missing_or_invalid_volume_in_window")
    baseline = median([c.volume for c in window[:-1]])
    if baseline == 0:
        return _fact("UNAVAILABLE", reason="previous_20_median_volume_is_zero")
    return _fact("AVAILABLE", {
        "ratio_to_prior_20_median": round(window[-1].volume/baseline, 8),
        "current_volume": window[-1].volume,
        "prior_20_median_volume": baseline,
    })


def _validate_series(series: CandleSeries, *, t0: str, mode: str,
                     allow_archived_duration_flags: bool) -> datetime:
    if not isinstance(series, CandleSeries) or series.timeframe not in {"D1", "H1"}:
        raise ValueError("exact CandleSeries D1 or H1 required")
    if not series.instrument.secid or not series.instrument.symbol:
        raise ValueError("exact instrument SECID required")
    if mode not in MODES and mode != "ARCHIVED_DURATION_FLAGS_RESEARCH_V2":
        raise ValueError("unsupported evidence mode")
    if allow_archived_duration_flags:
        if mode != "ARCHIVED_DURATION_FLAGS_RESEARCH_V2" or series.timeframe != "H1":
            raise ValueError("duration-flag research is H1-only")
        if series.source != ARCHIVED_SOURCE:
            raise ValueError("duration-flag research requires exact archived source label")
    elif mode == "ARCHIVED_DURATION_FLAGS_RESEARCH_V2":
        raise ValueError("archived mode requires dedicated research entrypoint")
    cutoff = _dt(t0)
    previous_end = None
    for bar in series.candles:
        start, end = _dt(bar.begin), _dt(bar.end)
        if start >= end or end > cutoff:
            raise ValueError("invalid bar interval or future candle relative to T0")
        if previous_end is not None and start < previous_end:
            raise ValueError("overlapping or unsorted candles")
        previous_end = end
        if type(bar.completed) is not bool:
            raise ValueError("non-boolean completed")
        if allow_archived_duration_flags:
            if bar.source != ARCHIVED_SOURCE:
                raise ValueError("archived candle provenance label required")
            # This checks the known normalization rule only. It does NOT
            # assert that the true primary completion status is known.
            duration = (end - start).total_seconds()
            if bar.completed != (duration in {3599.0, 3600.0}):
                raise ValueError("archived duration flag disagrees with receipt semantics")
            if (bar.available_at is not None or bar.observed_at is not None
                    or bar.available_at_confidence != "UNKNOWN"):
                raise ValueError("no constructed availability metadata in research archive")
        elif not bar.completed:
            raise ValueError("forming candle not admitted")
        present = (bar.open, bar.high, bar.low, bar.close)
        if any(v is not None and not _numeric(v) for v in present):
            raise ValueError("nonfinite, boolean or nonpositive price")
        if all(v is not None for v in present):
            if not (bar.low <= min(bar.open, bar.close) <=
                    max(bar.open, bar.close) <= bar.high):
                raise ValueError("inconsistent OHLC")
        if bar.volume is not None and not _numeric(bar.volume, positive=False):
            raise ValueError("invalid candle volume")
        if bar.available_at is not None and _dt(bar.available_at) > cutoff:
            raise ValueError("future available_at not admitted")
        if bar.observed_at is not None and _dt(bar.observed_at) > cutoff:
            raise ValueError("future observed_at not admitted")
        if mode == "T0_METADATA_GATED":
            if (bar.available_at is None or bar.observed_at is None
                    or bar.available_at_confidence not in {"EXACT", "DOCUMENTED"}):
                raise ValueError("T0 metadata unavailable: cannot claim as-known-at-T0")
            if (bar.source or "").startswith("NORMALIZED_RECONSTRUCTED"):
                raise ValueError("reconstructed source cannot pass T0 metadata gate")
    return cutoff


def _describe(series: CandleSeries, *, cutoff: datetime, mode: str,
              duration_research: bool) -> dict:
    """One common formula path: absolutely no candle dropping or normalization."""
    raw = json.dumps(series.to_dict(), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    candles = series.candles
    result = {
        "schema": VERSION,
        "symbol": series.instrument.symbol,
        "secid": series.instrument.secid,
        "timeframe": series.timeframe,
        "t0": cutoff.isoformat(),
        "mode": mode,
        "data_sha256": hashlib.sha256(raw).hexdigest(),
        "total_input_bars": len(candles),
        "total_completed_bars": sum(bar.completed for bar in candles),
        "duration_flagged_bars": sum(not bar.completed for bar in candles),
        "facts": {
            "confirmed_price_structure": _swings(candles),
            "wilder_atr14": _wilder(candles),
            "relative_volume_prior20": _relative_volume(
                candles, "VOLUME" in series.instrument.data_capabilities
            ),
        },
        "directional_prediction": None,
        "market_state_regime": None,
        "control": "UNKNOWN",
        "route": "UNAVAILABLE",
        "independent_first_receipt_attested": False,
        "probability_calibrated": False,
        "forecast_or_trading_authorized": False,
        "strict_pit_admitted": False,
    }
    if duration_research:
        result["archived_completed_flags_preserved"] = True
        result["original_completion_unknown_for_duration_flags"] = True
        result["use_of_duration_flagged_bars"] = "DESCRIPTIVE_RECONSTRUCTION_ONLY"
        result["calendar_session_eligibility_attested"] = False
    return result


def build_market_structure_facts(
    series: CandleSeries, *, t0: str,
    mode: str = "RECONSTRUCTED_RESEARCH_ONLY",
) -> dict:
    """Strict completed-only input, including reconstructed research mode.

    Both modes reject completed=False. T0 metadata gate checks source *claims*,
    not independent receipt. No directional prediction or Forecast admission.
    """
    cutoff = _validate_series(
        series, t0=t0, mode=mode, allow_archived_duration_flags=False
    )
    return _describe(series, cutoff=cutoff, mode=mode, duration_research=False)


def build_archived_h1_descriptive_facts(series: CandleSeries, *, t0: str) -> dict:
    """Explicit weak admission for the single duration-normalized G2 H1 archive.

    A completed=False flag remains False and is still counted. This separate
    research-only function does not and cannot turn it into a completed price
    observation for strict Forecast or retrospective PIT validation. A caller
    must check the frozen receipt and raw input SHA independently.
    """
    mode = "ARCHIVED_DURATION_FLAGS_RESEARCH_V2"
    cutoff = _validate_series(
        series, t0=t0, mode=mode, allow_archived_duration_flags=True
    )
    return _describe(series, cutoff=cutoff, mode=mode, duration_research=True)
