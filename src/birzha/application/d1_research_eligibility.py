"""Conservative eligibility for reconstructed D1 research windows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import HistoricalCandleStore


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
    UNVERIFIED_WARMUP = "UNVERIFIED_WARMUP"


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


@dataclass(frozen=True, slots=True)
class ExactD1Candle:
    """Candle and its real contract identity; Candle alone lacks SECID."""

    secid: str
    candle: Candle


@dataclass(frozen=True, slots=True)
class VerifiedD1WarmupEvidence:
    """One complete, persisted and range-verified exact-SECID calendar generation."""

    secid: str
    evidence_key: str
    origin: str
    expected_dates: tuple[str, ...]


def load_verified_d1_warmup_evidence(
    store: "HistoricalCandleStore",
    secid: str,
    evidence_key: str,
) -> VerifiedD1WarmupEvidence | None:
    """Load an exact generation by its key, never by a guessed 'latest' marker.

    Only the rows and certificate under the *same* generation key qualify.
    Legacy CONTRACT_WARMUP_V2_ACTIVITY price ranges are not session evidence.
    """
    from birzha.application.warmup_session_evidence import warmup_d1_evidence_key

    rows = store.stored_session_contracts(
        evidence_key, "0001-01-01", "9999-12-31"
    )
    if not rows or any(row_secid != secid for _, row_secid in rows):
        return None
    dates = tuple(day for day, _ in rows)
    if len(dates) != len(set(dates)) or dates != tuple(sorted(dates)):
        return None
    for origin in ("CAPTURED_AT_SYNC", "RECONSTRUCTED_MOEX"):
        if warmup_d1_evidence_key(secid, dates, origin=origin) == evidence_key:
            if not store.is_session_range_verified(
                evidence_key, dates[0], dates[-1]
            ):
                return None
            return VerifiedD1WarmupEvidence(
                secid=secid,
                evidence_key=evidence_key,
                origin=origin,
                expected_dates=dates,
            )
    return None


def evaluate_d1_research_window_with_warmup(
    instrument: "Instrument",
    candles: tuple[ExactD1Candle, ...],
    verified_sessions: tuple[VerifiedD1Session, ...] | None,
    *,
    candidate_date: str,
    warmup: VerifiedD1WarmupEvidence | None,
    required_bars: int = FULL_FEATURE_HISTORY_BARS,
) -> D1ResearchEligibilityResult:
    """Opt-in reconstructed D1 eligibility with verified same-SECID preactive dates.

    The candidate is explicit. Later candles are ignored, never consumed as
    history for T. No roots/continuous series are stitched across rollover.
    """
    from birzha.application.warmup_session_evidence import warmup_d1_evidence_key

    def result(reason: D1ResearchEligibilityReason) -> D1ResearchEligibilityResult:
        return D1ResearchEligibilityResult(
            eligible=reason is D1ResearchEligibilityReason.ELIGIBLE,
            reason=reason,
            required_bars=required_bars,
            candidate_date=candidate_date,
        )

    if required_bars < 1 or not _is_iso_date(candidate_date):
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)
    if _wrong_gold_identity(CandleSeries(instrument, "D1", ())):
        return result(D1ResearchEligibilityReason.WRONG_INSTRUMENT_IDENTITY)
    if not verified_sessions or not _valid_calendar(verified_sessions):
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)
    active = {s.trade_date: s.secid for s in verified_sessions}
    if candidate_date not in active:
        return result(D1ResearchEligibilityReason.OUTSIDE_VERIFIED_CALENDAR)
    if active[candidate_date] != instrument.secid:
        return result(D1ResearchEligibilityReason.CROSS_CONTRACT_WINDOW)

    # Take the uninterrupted active contract segment ending exactly at T.
    before_t = tuple(s for s in verified_sessions if s.trade_date <= candidate_date)
    current_active_reversed: list[str] = []
    for session in reversed(before_t):
        if session.secid != instrument.secid:
            break
        current_active_reversed.append(session.trade_date)
    current_active = tuple(reversed(current_active_reversed))
    if not current_active or current_active[-1] != candidate_date:
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)

    needed_warmup = max(0, required_bars - len(current_active))
    if needed_warmup:
        if warmup is None:
            return result(D1ResearchEligibilityReason.UNVERIFIED_WARMUP)
        if (
            warmup.secid != instrument.secid
            or warmup.origin not in ("CAPTURED_AT_SYNC", "RECONSTRUCTED_MOEX")
            or not warmup.expected_dates
        ):
            return result(D1ResearchEligibilityReason.UNVERIFIED_WARMUP)
        try:
            key = warmup_d1_evidence_key(
                warmup.secid, warmup.expected_dates, origin=warmup.origin
            )
        except ValueError:
            return result(D1ResearchEligibilityReason.UNVERIFIED_WARMUP)
        if key != warmup.evidence_key:
            return result(D1ResearchEligibilityReason.UNVERIFIED_WARMUP)
        if (
            tuple(sorted(set(warmup.expected_dates))) != warmup.expected_dates
            or warmup.expected_dates[-1] >= current_active[0]
        ):
            return result(D1ResearchEligibilityReason.UNVERIFIED_WARMUP)
        expected = warmup.expected_dates[-needed_warmup:] + current_active
    else:
        expected = current_active[-required_bars:]

    if len(expected) != required_bars:
        return result(D1ResearchEligibilityReason.INSUFFICIENT_HISTORY)

    # Ignore future bars. Validate the exact candidate window, without using
    # missing-day compression or substituting an older bar for a missing date.
    effective = tuple(
        bar for bar in candles
        if _date_of(bar.candle) is not None
        and _date_of(bar.candle) <= candidate_date
    )
    actual = tuple(_date_of(item.candle) for item in effective)
    if any(item is None for item in actual):
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)
    if len(actual) != len(set(actual)) or actual != tuple(sorted(actual)):
        return result(D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE)
    if any(bar.secid != instrument.secid for bar in effective):
        return result(D1ResearchEligibilityReason.CROSS_CONTRACT_WINDOW)
    if len(effective) != len(expected) or actual != expected:
        return result(D1ResearchEligibilityReason.MISSING_BAR)
    if any(not bar.candle.completed for bar in effective):
        return result(D1ResearchEligibilityReason.INCOMPLETE_BAR)
    if any(not _has_required_ohlc(bar.candle) for bar in effective):
        return result(D1ResearchEligibilityReason.MISSING_REQUIRED_VALUE)
    return result(D1ResearchEligibilityReason.ELIGIBLE)
