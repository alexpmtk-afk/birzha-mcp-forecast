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


# Opt-in preactive research evaluation, separate from unchanged active-only path.
from birzha.application.d1_research_eligibility import (
    ExactD1Candle,
    VerifiedD1WarmupEvidence,
    evaluate_d1_research_window_with_warmup,
    load_verified_d1_warmup_evidence,
)
from birzha.application.warmup_session_evidence import warmup_d1_evidence_key
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


def _preactive_case(
    secid: str = "BRM6", root: str = "BR", *,
    evidence: bool = True,
) -> tuple[Instrument, tuple[ExactD1Candle, ...], tuple[VerifiedD1Session, ...],
           VerifiedD1WarmupEvidence | None]:
    instrument = _instrument(symbol=root, secid=secid, asset_class="future", root_symbol=root)
    previous = tuple(f"2025-01-{day:02d}" for day in range(1, 21))
    active = (VerifiedD1Session("2025-01-21", secid),)
    days = previous + ("2025-01-21",)
    candles = tuple(ExactD1Candle(secid, _candle(day)) for day in range(1, 22))
    warmup = (VerifiedD1WarmupEvidence(
        secid=secid, origin="RECONSTRUCTED_MOEX",
        evidence_key=warmup_d1_evidence_key(
            secid, previous, origin="RECONSTRUCTED_MOEX"
        ), expected_dates=previous,
    ) if evidence else None)
    return instrument, candles, active, warmup


def _check_preactive(
    instrument, candles, active, warmup,
    candidate_date="2025-01-21",
) -> D1ResearchEligibilityReason:
    return evaluate_d1_research_window_with_warmup(
        instrument, candles, active, candidate_date=candidate_date, warmup=warmup
    ).reason


def test_preactive_br_first_active_is_eligible_with_verified_same_secid() -> None:
    assert _check_preactive(*_preactive_case()) == D1ResearchEligibilityReason.ELIGIBLE


def test_preactive_requires_evidence_and_matching_generation() -> None:
    instrument, candles, active, warmup = _preactive_case()
    assert _check_preactive(instrument, candles, active, None) == D1ResearchEligibilityReason.UNVERIFIED_WARMUP
    from dataclasses import replace
    assert warmup is not None
    assert _check_preactive(instrument, candles, active, replace(warmup, evidence_key="invalid")) == D1ResearchEligibilityReason.UNVERIFIED_WARMUP
    assert _check_preactive(instrument, candles, active, replace(warmup, secid="BRZ5")) == D1ResearchEligibilityReason.UNVERIFIED_WARMUP


def test_preactive_middle_hole_cannot_be_replaced_by_older_bar() -> None:
    instrument, candles, active, warmup = _preactive_case()
    missing = tuple(bar for bar in candles if not bar.candle.begin.startswith("2025-01-10"))
    assert _check_preactive(instrument, missing, active, warmup) == D1ResearchEligibilityReason.MISSING_BAR


def test_preactive_rejects_wrong_contract_inside_window() -> None:
    from dataclasses import replace
    instrument, candles, active, warmup = _preactive_case()
    altered = list(candles)
    altered[5] = replace(altered[5], secid="BRZ5")
    assert _check_preactive(instrument, tuple(altered), active, warmup) == D1ResearchEligibilityReason.CROSS_CONTRACT_WINDOW


def test_preactive_rejects_incomplete_and_missing_ohlc() -> None:
    from dataclasses import replace
    instrument, candles, active, warmup = _preactive_case()
    for field, value, reason in [
        ("completed", False, D1ResearchEligibilityReason.INCOMPLETE_BAR),
        ("high", None, D1ResearchEligibilityReason.MISSING_REQUIRED_VALUE),
    ]:
        altered = list(candles)
        altered[5] = replace(altered[5], candle=replace(altered[5].candle, **{field: value}))
        assert _check_preactive(instrument, tuple(altered), active, warmup) == reason


def test_preactive_si_gold_and_equity_gold_identity() -> None:
    for secid, root in (("SiM6", "Si"), ("GDM6", "GOLD")):
        assert _check_preactive(*_preactive_case(secid, root)) == D1ResearchEligibilityReason.ELIGIBLE
    inst, candles, active, warmup = _preactive_case("GOLD", "GOLD")
    wrong = _instrument(symbol="GOLD", secid="GOLD", asset_class="equity")
    assert _check_preactive(wrong, candles, active, warmup) == D1ResearchEligibilityReason.WRONG_INSTRUMENT_IDENTITY


def test_preactive_future_cannot_influence_past_candidate() -> None:
    instrument, candles, active, warmup = _preactive_case()
    later = ExactD1Candle("BRM6", _candle(22))
    assert _check_preactive(instrument, candles+(later,), active, warmup) == D1ResearchEligibilityReason.ELIGIBLE
    assert _check_preactive(instrument, candles[:-1], active, warmup) == D1ResearchEligibilityReason.MISSING_BAR


def test_preactive_duplicate_or_unsorted_bars_fail_closed() -> None:
    instrument, candles, active, warmup = _preactive_case()
    assert _check_preactive(instrument, candles+(candles[0],), active, warmup) == D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE
    assert _check_preactive(instrument, tuple(reversed(candles)), active, warmup) == D1ResearchEligibilityReason.UNVERIFIED_SEQUENCE


def test_loaded_evidence_requires_exact_stored_sessions_and_verified_range() -> None:
    dates = tuple(f"2025-01-{day:02d}" for day in range(1, 21))
    key = warmup_d1_evidence_key("BRM6", dates, origin="CAPTURED_AT_SYNC")
    store = DuckDBHistoricalCandleStore()
    assert load_verified_d1_warmup_evidence(store, "BRM6", key) is None
    store.record_sessions(key, "BRM6", dates)
    assert load_verified_d1_warmup_evidence(store, "BRM6", key) is None
    store.mark_session_range_verified(key, dates[0], dates[-1])
    evidence = load_verified_d1_warmup_evidence(store, "BRM6", key)
    assert evidence is not None and evidence.expected_dates == dates
    assert load_verified_d1_warmup_evidence(store, "BRZ5", key) is None
    assert load_verified_d1_warmup_evidence(store, "BRM6", "BRM6#CONTRACT_WARMUP_V2_ACTIVITY") is None
    store.close()


def test_preactive_does_not_change_existing_sber_active_only_eligibility() -> None:
    days = tuple(range(1, 22))
    expected = _calendar(days)
    original = evaluate_d1_research_window(_series(tuple(_candle(day) for day in days)), expected)
    assert original.reason == D1ResearchEligibilityReason.ELIGIBLE
