from __future__ import annotations

from birzha.application.d1_research_eligibility import (
    D1ResearchEligibilityReason,
    VerifiedD1Session,
    evaluate_d1_research_window,
)
from birzha.domain.market import Candle, CandleSeries, Instrument


def _instrument(
    *,
    symbol: str = "SBER",
    secid: str = "SBER",
    asset_class: str = "equity",
    root_symbol: str | None = None,
) -> Instrument:
    return Instrument(
        symbol=symbol,
        secid=secid,
        board="RFUD" if asset_class == "future" else "TQBR",
        engine="futures" if asset_class == "future" else "stock",
        market="forts" if asset_class == "future" else "shares",
        asset_class=asset_class,  # type: ignore[arg-type]
        root_symbol=root_symbol,
    )


def _candle(
    day: int, *, close: float = 100.0, completed: bool = True, year: int = 2025
) -> Candle:
    date = f"{year}-01-{day:02d}"
    return Candle(
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        value=1.0,
        volume=1.0,
        begin=f"{date} 00:00:00",
        end=f"{date} 23:59:59",
        completed=completed,
    )


def _series(
    candles: tuple[Candle, ...], *, instrument: Instrument | None = None
) -> CandleSeries:
    return CandleSeries(
        instrument=instrument or _instrument(), timeframe="D1", candles=candles
    )


def _calendar(
    days: tuple[int, ...], *, secid: str = "SBER", year: int = 2025
) -> tuple[VerifiedD1Session, ...]:
    return tuple(
        VerifiedD1Session(f"{year}-01-{day:02d}", secid) for day in days
    )


def test_complete_sber_21_bar_window_is_eligible() -> None:
    days = tuple(range(1, 22))
    result = evaluate_d1_research_window(
        _series(tuple(_candle(day) for day in days)), _calendar(days)
    )
    assert result.eligible is True
    assert result.reason is D1ResearchEligibilityReason.ELIGIBLE
    assert result.candidate_date == "2025-01-21"


def test_internal_missing_bar_is_excluded_without_compressing_series() -> None:
    calendar_days = tuple(range(1, 22))
    candle_days = tuple(day for day in calendar_days if day != 10)
    result = evaluate_d1_research_window(
        _series(tuple(_candle(day) for day in candle_days)), _calendar(calendar_days)
    )
    assert result.reason is D1ResearchEligibilityReason.MISSING_BAR


def test_missing_required_ohlc_is_excluded() -> None:
    days = tuple(range(1, 22))
    candles = list(_candle(day) for day in days)
    candles[7] = Candle(
        open=99.0,
        high=101.0,
        low=98.0,
        close=None,
        value=1.0,
        volume=1.0,
        begin="2025-01-08 00:00:00",
        end="2025-01-08 23:59:59",
    )
    result = evaluate_d1_research_window(_series(tuple(candles)), _calendar(days))
    assert result.reason is D1ResearchEligibilityReason.MISSING_REQUIRED_VALUE


def test_candle_outside_verified_calendar_is_excluded() -> None:
    days = tuple(range(1, 22))
    result = evaluate_d1_research_window(
        _series(tuple(_candle(day) for day in days)), _calendar(days[:-1])
    )
    assert result.reason is D1ResearchEligibilityReason.OUTSIDE_VERIFIED_CALENDAR


def test_futures_window_crossing_rollover_is_excluded() -> None:
    days = tuple(range(1, 22))
    sessions = _calendar(days[:10], secid="SiZ4") + _calendar(days[10:], secid="SiH5")
    result = evaluate_d1_research_window(
        _series(
            tuple(_candle(day) for day in days),
            instrument=_instrument(
                symbol="Si", secid="SiH5", asset_class="future", root_symbol="Si"
            ),
        ),
        sessions,
    )
    assert result.reason is D1ResearchEligibilityReason.CROSS_CONTRACT_WINDOW


def test_first_twenty_active_sessions_after_rollover_are_not_eligible() -> None:
    days = tuple(range(1, 22))
    sessions = (VerifiedD1Session("2024-12-31", "SiZ4"),) + _calendar(
        days[:20], secid="SiH5"
    )
    result = evaluate_d1_research_window(
        _series(
            tuple(_candle(day) for day in days[:20]),
            instrument=_instrument(
                symbol="Si", secid="SiH5", asset_class="future", root_symbol="Si"
            ),
        ),
        sessions,
    )
    assert result.reason is D1ResearchEligibilityReason.CROSS_CONTRACT_WINDOW


def test_twenty_first_active_future_session_is_eligible() -> None:
    days = tuple(range(1, 22))
    result = evaluate_d1_research_window(
        _series(
            tuple(_candle(day) for day in days),
            instrument=_instrument(
                symbol="Si", secid="SiH5", asset_class="future", root_symbol="Si"
            ),
        ),
        _calendar(days, secid="SiH5"),
    )
    assert result.reason is D1ResearchEligibilityReason.ELIGIBLE


def test_gold_equity_is_excluded_from_gold_futures_root() -> None:
    days = tuple(range(1, 22))
    result = evaluate_d1_research_window(
        _series(
            tuple(_candle(day) for day in days),
            instrument=_instrument(symbol="GOLD", secid="GOLD", asset_class="equity"),
        ),
        _calendar(days, secid="GOLD"),
    )
    assert result.reason is D1ResearchEligibilityReason.WRONG_INSTRUMENT_IDENTITY


def test_suspicious_imoex_date_outside_calendar_is_not_eligible() -> None:
    days = tuple(range(1, 22))
    candles = list(_candle(day, year=2022) for day in days)
    candles[6] = Candle(
        open=3772.04,
        high=3772.04,
        low=3772.04,
        close=3772.04,
        value=0.0,
        volume=0.0,
        begin="2022-01-07 00:00:00",
        end="2022-01-07 23:59:59",
    )
    calendar = tuple(
        VerifiedD1Session(candle.begin[:10], "IMOEX")
        for candle in candles
        if candle.begin[:10] != "2022-01-07"
    )
    result = evaluate_d1_research_window(
        _series(
            tuple(candles),
            instrument=_instrument(symbol="IMOEX", secid="IMOEX", asset_class="index"),
        ),
        calendar,
    )
    assert result.reason is D1ResearchEligibilityReason.OUTSIDE_VERIFIED_CALENDAR


def test_result_is_deterministic_and_unverified_calendar_is_explicit() -> None:
    days = tuple(range(1, 22))
    series = _series(tuple(_candle(day) for day in days))
    first = evaluate_d1_research_window(series, _calendar(days))
    second = evaluate_d1_research_window(series, _calendar(days))
    unverified = evaluate_d1_research_window(series, None)
    assert first == second
    assert unverified.reason is D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE


def test_incomplete_candle_is_excluded() -> None:
    days = tuple(range(1, 22))
    candles = list(_candle(day) for day in days)
    candles[-1] = _candle(21, completed=False)

    result = evaluate_d1_research_window(_series(tuple(candles)), _calendar(days))

    assert result.reason is D1ResearchEligibilityReason.INCOMPLETE_BAR


def test_duplicate_calendar_date_is_unverified() -> None:
    days = tuple(range(1, 22))
    calendar = _calendar(days) + (VerifiedD1Session("2025-01-21", "SBER"),)

    result = evaluate_d1_research_window(
        _series(tuple(_candle(day) for day in days)), calendar
    )
    assert result.reason is D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE
