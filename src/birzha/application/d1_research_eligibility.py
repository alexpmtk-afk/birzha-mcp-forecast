"""Conservative eligibility for reconstructed D1 research windows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from birzha.domain.market import Candle, CandleSeries


RECONSTRUCTED_D1_RESEARCH_ELIGIBILITY_VERSION = "RECONSTRUCTED_D1_RESEARCH_ELIGIBILITY_V0"
FULL_FEATURE_HISTORY_BARS = 21


class D1ResearchEligibilityReason(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MISSING_BAR = "MISSING_BAR"
    MISSING_REQUIRED_VALUE = "MISSING_REQUIRED_VALUE"
    INCOMPLETE_BAR = "INCOMPLETE_BAR"
    OUTSIDE_VERIFIED_CALENDAR = "OUTSIDE_VERIFIED_CALENDAR"
    CROSS_CONTRACT_WINDOW = "CROSS_CONTRACT_WINDOW"
    WRONG_INSTRUMENT_IDENTITY = "WRONG_INSTRUMENT_IDENTITY"
    UNVERIFIED_SEQUENCE = "UNVERIFIED_SEQUENCE"


@dataclass(frozen=True, slots=True)
class VerifiedD1Session:
    """One date and its exact active SECID from a verified D1 calendar."""

    trade_date: str
    secid: str


@dataclass(frozen=True, slots=True)
class D1ResearchEligibilityResult:
    eligible: bool
    reason: D1ResearchEligibilityReason
    required_bars: int
    candidate_date: str | None


def evaluate_d1_research_window(
    series: CandleSeries,
    verified_sessions: tuple[VerifiedD1Session, ...] | None,
    *,
    required_bars: int = FULL_FEATURE_HISTORY_BARS,
) -> D1ResearchEligibilityResult:
    """Evaluate one exact-instrument D1 window without reading external state."""
    candidate_date = _date_of(series.candles[-1]) if series.candles else None

    def result(reason: D1ResearchEligibilityReason) -> D1ResearchEligibilityResult:
        return D1ResearchEligibilityResult(
            eligible=reason is D1ResearchEligibilityReason.ELIGIBLE,
            reason=reason,
            required_bars=required_bars,
            candidate_date=candidate_date,
        )

    if series.timeframe.upper() != "D1" or required_bars < 1:
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)
    if _wrong_gold_identity(series):
        return result(D1ResearchEligibilityReason.WRONG_INSTRUMENT_IDENTITY)
    if not verified_sessions or not _valid_calendar(verified_sessions):
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)
    if not series.candles or candidate_date is None:
        return result(D1ResearchEligibilityReason.INSUFFICIENT_HISTORY)

    calendar = {item.trade_date: item.secid for item in verified_sessions}
    candle_dates = tuple(_date_of(candle) for candle in series.candles)
    if any(item is None for item in candle_dates):
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)
    actual_dates = tuple(item for item in candle_dates if item is not None)
    if len(actual_dates) != len(set(actual_dates)) or actual_dates != tuple(sorted(actual_dates)):
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)
    if any(item not in calendar for item in actual_dates):
        return result(D1ResearchEligibilityReason.OUTSIDE_VERIFIED_CALENDAR)
    if candidate_date not in calendar:
        return result(D1ResearchEligibilityReason.OUTSIDE_VERIFIED_CALENDAR)

    sessions_to_candidate = tuple(
        item for item in verified_sessions if item.trade_date <= candidate_date
    )
    if len(sessions_to_candidate) < required_bars:
        return result(D1ResearchEligibilityReason.INSUFFICIENT_HISTORY)
    expected_window = sessions_to_candidate[-required_bars:]
    if any(item.secid != series.instrument.secid for item in expected_window):
        return result(D1ResearchEligibilityReason.CROSS_CONTRACT_WINDOW)
    expected_dates = tuple(item.trade_date for item in expected_window)
    if len(actual_dates) != required_bars or actual_dates != expected_dates:
        return result(D1ResearchEligibilityReason.MISSING_BAR)
    if any(not candle.completed for candle in series.candles):
        return result(D1ResearchEligibilityReason.INCOMPLETE_BAR)
    if any(not _has_required_ohlc(candle) for candle in series.candles):
        return result(D1ResearchEligibilityReason.MISSING_REQUIRED_VALUE)
    return result(D1ResearchEligibilityReason.ELIGIBLE)


def _wrong_gold_identity(series: CandleSeries) -> bool:
    root = (series.instrument.root_symbol or series.instrument.symbol).upper()
    if root != "GOLD":
        return False
    return (
        series.instrument.asset_class != "future"
        or not series.instrument.secid.upper().startswith("GD")
    )


def _valid_calendar(sessions: tuple[VerifiedD1Session, ...]) -> bool:
    dates = tuple(item.trade_date for item in sessions)
    return (
        all(
            _is_iso_date(item) and bool(session.secid)
            for item, session in zip(dates, sessions)
        )
        and dates == tuple(sorted(dates))
        and len(dates) == len(set(dates))
    )


def _date_of(candle: Candle) -> str | None:
    candidate = candle.begin[:10]
    return candidate if _is_iso_date(candidate) else None


def _is_iso_date(value: str) -> bool:
    return len(value) == 10 and value[4] == "-" and value[7] == "-"


def _has_required_ohlc(candle: Candle) -> bool:
    return all(
        value is not None
        for value in (candle.open, candle.high, candle.low, candle.close)
    )
