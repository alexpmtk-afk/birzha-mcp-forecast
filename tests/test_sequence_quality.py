from datetime import date

from birzha.application.sequence_quality import audit_candle_sequence
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.domain.sequence_quality import (
    CandleIntervalKey,
    CandleSequenceScheduleEvidence,
    SequenceWindowEvidence,
)


SBER = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
)
SI_ROOT = Instrument(
    symbol="Si",
    secid="Si",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="Si",
)


def _candle(begin: str, end: str, close: float = 100.0) -> Candle:
    return Candle(
        open=close,
        close=close,
        high=close,
        low=close,
        value=1000.0,
        volume=10.0,
        begin=begin,
        end=end,
    )


def _interval(
    secid: str,
    begin: str,
    end: str,
    *,
    trade_date: str,
    disposition: str = "BAR_REQUIRED",
    reason: str = "TRADE_CONFIRMED",
    rule_version: str = "schedule-v1",
) -> SequenceWindowEvidence:
    return SequenceWindowEvidence(
        trade_date=trade_date,
        interval=CandleIntervalKey(secid, begin, end),
        disposition=disposition,  # type: ignore[arg-type]
        reason=reason,
        source_ref="fixture:verified-evidence",
        rule_version=rule_version,
    )


def _evidence(
    instrument_key: str,
    timeframe: str,
    windows: tuple[SequenceWindowEvidence, ...],
    *,
    coverage_from: str,
    coverage_till: str,
    coverage_status: str = "COMPLETE",
) -> CandleSequenceScheduleEvidence:
    return CandleSequenceScheduleEvidence(
        instrument_key=instrument_key,
        timeframe=timeframe,
        coverage_status=coverage_status,  # type: ignore[arg-type]
        coverage_from=coverage_from,
        coverage_till=coverage_till,
        source="fixture:explicit-schedule-and-activity",
        version="fixture-v1",
        timezone="Europe/Moscow",
        windows=windows,
    )


def _series(
    instrument: Instrument,
    timeframe: str,
    candles: tuple[Candle, ...],
) -> CandleSeries:
    return CandleSeries(instrument=instrument, timeframe=timeframe, candles=candles)


def test_complete_d1_sequence_passes() -> None:
    windows = (
        _interval("SBER", "2026-09-01 00:00:00", "2026-09-01 23:59:59", trade_date="2026-09-01"),
        _interval("SBER", "2026-09-02 00:00:00", "2026-09-02 23:59:59", trade_date="2026-09-02"),
    )
    result = audit_candle_sequence(
        instrument=SBER,
        timeframe="D1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 2),
        candle_series=_series(
            SBER,
            "D1",
            (
                _candle("2026-09-01 00:00:00", "2026-09-01 23:59:59"),
                _candle("2026-09-02 00:00:00", "2026-09-02 23:59:59"),
            ),
        ),
        schedule_evidence=_evidence(
            "SBER", "D1", windows, coverage_from="2026-09-01", coverage_till="2026-09-02"
        ),
    )

    assert result.status == "PASS"
    assert result.expected_count == 2
    assert result.observed_count == result.matched_count == 2
    assert result.missing_intervals == ()


def test_d1_actual_last_trade_end_does_not_create_false_gap() -> None:
    windows = (
        _interval(
            "SBER",
            "2026-09-01 00:00:00",
            "2026-09-01 23:59:59",
            trade_date="2026-09-01",
        ),
    )
    result = audit_candle_sequence(
        instrument=SBER,
        timeframe="D1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 1),
        candle_series=_series(
            SBER,
            "D1",
            (_candle("2026-09-01 00:00:00", "2026-09-01 18:39:42"),),
        ),
        schedule_evidence=_evidence(
            "SBER",
            "D1",
            windows,
            coverage_from="2026-09-01",
            coverage_till="2026-09-01",
        ),
    )

    assert result.status == "PASS"
    assert result.expected_count == result.observed_count == result.matched_count == 1


def test_missing_d1_is_degraded_only_when_activity_proves_bar_expected() -> None:
    windows = (
        _interval("SBER", "2026-09-01 00:00:00", "2026-09-01 23:59:59", trade_date="2026-09-01"),
        _interval("SBER", "2026-09-02 00:00:00", "2026-09-02 23:59:59", trade_date="2026-09-02"),
    )
    result = audit_candle_sequence(
        instrument=SBER,
        timeframe="D1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 2),
        candle_series=_series(
            SBER,
            "D1",
            (_candle("2026-09-01 00:00:00", "2026-09-01 23:59:59"),),
        ),
        schedule_evidence=_evidence(
            "SBER", "D1", windows, coverage_from="2026-09-01", coverage_till="2026-09-02"
        ),
    )

    assert result.status == "DEGRADED"
    assert result.expected_count == 2
    assert result.matched_count == 1
    assert len(result.missing_intervals) == 1


def test_weekend_and_holiday_are_explicit_normal_breaks_not_gaps() -> None:
    windows = (
        _interval("SBER", "2026-09-04 00:00:00", "2026-09-04 23:59:59", trade_date="2026-09-04"),
        _interval(
            "SBER", "2026-09-05 00:00:00", "2026-09-07 23:59:59",
            trade_date="2026-09-05", disposition="NO_BAR_EXPECTED", reason="WEEKEND",
        ),
        _interval(
            "SBER", "2026-09-08 00:00:00", "2026-09-08 23:59:59",
            trade_date="2026-09-08", disposition="NO_BAR_EXPECTED", reason="HOLIDAY",
        ),
    )
    result = audit_candle_sequence(
        instrument=SBER,
        timeframe="D1",
        period_start=date(2026, 9, 4),
        period_end=date(2026, 9, 8),
        candle_series=_series(
            SBER,
            "D1",
            (_candle("2026-09-04 00:00:00", "2026-09-04 23:59:59"),),
        ),
        schedule_evidence=_evidence(
            "SBER", "D1", windows, coverage_from="2026-09-04", coverage_till="2026-09-08"
        ),
    )

    assert result.status == "PASS"
    assert result.expected_count == 1
    assert len(result.normal_breaks) == 2
    assert result.missing_intervals == ()


def test_no_trade_interval_is_not_misreported_as_missing_candle() -> None:
    windows = (
        _interval("SBER", "2026-09-01 10:00:00", "2026-09-01 10:14:59", trade_date="2026-09-01"),
        _interval(
            "SBER", "2026-09-01 10:15:00", "2026-09-01 10:29:59",
            trade_date="2026-09-01", disposition="NO_BAR_EXPECTED", reason="NO_TRADES",
        ),
    )
    result = audit_candle_sequence(
        instrument=SBER,
        timeframe="M15",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 1),
        candle_series=_series(
            SBER,
            "M15",
            (_candle("2026-09-01 10:00:00", "2026-09-01 10:14:59"),),
        ),
        schedule_evidence=_evidence(
            "SBER", "M15", windows, coverage_from="2026-09-01", coverage_till="2026-09-01"
        ),
    )

    assert result.status == "PASS"
    assert result.expected_count == 1
    assert len(result.no_trade_intervals) == 1
    assert result.missing_intervals == ()


def test_futures_rollover_uses_contract_identity_and_does_not_create_gap() -> None:
    old = Instrument(
        symbol="SiU6", secid="SiU6", board="RFUD", engine="futures",
        market="forts", asset_class="future", root_symbol="Si",
    )
    new = Instrument(
        symbol="SiZ6", secid="SiZ6", board="RFUD", engine="futures",
        market="forts", asset_class="future", root_symbol="Si",
    )
    windows = (
        _interval("SIU6", "2026-09-01 10:00:00", "2026-09-01 10:59:59", trade_date="2026-09-01"),
        _interval("SIZ6", "2026-09-02 10:00:00", "2026-09-02 10:59:59", trade_date="2026-09-02"),
    )
    result = audit_candle_sequence(
        instrument=SI_ROOT,
        timeframe="H1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 2),
        candle_series=(
            _series(old, "H1", (_candle("2026-09-01 10:00:00", "2026-09-01 10:59:59"),)),
            _series(new, "H1", (_candle("2026-09-02 10:00:00", "2026-09-02 10:59:59"),)),
        ),
        schedule_evidence=_evidence(
            "SI",
            "H1",
            windows,
            coverage_from="2026-09-01",
            coverage_till="2026-09-02",
        ),
    )

    assert result.status == "PASS"
    assert result.expected_count == result.observed_count == result.matched_count == 2


def test_intraday_session_break_is_not_an_expected_bar() -> None:
    windows = (
        _interval("SBER", "2026-09-01 10:00:00", "2026-09-01 10:59:59", trade_date="2026-09-01"),
        _interval(
            "SBER", "2026-09-01 11:00:00", "2026-09-01 13:59:59",
            trade_date="2026-09-01", disposition="NO_BAR_EXPECTED", reason="SESSION_BREAK",
        ),
        _interval("SBER", "2026-09-01 14:00:00", "2026-09-01 14:59:59", trade_date="2026-09-01"),
    )
    result = audit_candle_sequence(
        instrument=SBER,
        timeframe="H1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 1),
        candle_series=_series(
            SBER,
            "H1",
            (
                _candle("2026-09-01 10:00:00", "2026-09-01 10:59:59"),
                _candle("2026-09-01 14:00:00", "2026-09-01 14:59:59"),
            ),
        ),
        schedule_evidence=_evidence(
            "SBER", "H1", windows, coverage_from="2026-09-01", coverage_till="2026-09-01"
        ),
    )

    assert result.status == "PASS"
    assert result.expected_count == 2
    assert len(result.normal_breaks) == 1


def test_missing_proven_interval_is_degraded_but_unknown_schedule_is_unverified() -> None:
    windows = (
        _interval("SBER", "2026-09-01 10:00:00", "2026-09-01 10:59:59", trade_date="2026-09-01"),
        _interval("SBER", "2026-09-01 11:00:00", "2026-09-01 11:59:59", trade_date="2026-09-01"),
    )
    partial = _evidence(
        "SBER", "H1", windows, coverage_from="2026-09-01", coverage_till="2026-09-01",
        coverage_status="UNKNOWN",
    )
    unverified = audit_candle_sequence(
        instrument=SBER,
        timeframe="H1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 1),
        candle_series=_series(
            SBER, "H1", (_candle("2026-09-01 10:00:00", "2026-09-01 10:59:59"),)
        ),
        schedule_evidence=partial,
    )
    unknown = audit_candle_sequence(
        instrument=SBER,
        timeframe="H1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 1),
        candle_series=_series(
            SBER, "H1", (_candle("2026-09-01 10:00:00", "2026-09-01 10:59:59"),)
        ),
        schedule_evidence=None,
    )

    assert unverified.status == "DEGRADED"
    assert len(unverified.missing_intervals) == 1
    assert unverified.expected_count is None
    assert unknown.status == "UNVERIFIED"
    assert unknown.expected_count is None
    assert unknown.missing_intervals == ()


def test_missing_source_data_is_unverified_not_zero_observed_or_confirmed_gap() -> None:
    windows = (
        _interval("SBER", "2026-09-01 00:00:00", "2026-09-01 23:59:59", trade_date="2026-09-01"),
    )
    result = audit_candle_sequence(
        instrument=SBER,
        timeframe="D1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 1),
        candle_series=None,
        schedule_evidence=_evidence(
            "SBER", "D1", windows, coverage_from="2026-09-01", coverage_till="2026-09-01"
        ),
    )

    assert result.status == "UNVERIFIED"
    assert result.observed_count is None
    assert result.matched_count is None
    assert result.expected_count == 1
    assert result.missing_intervals == ()


def test_schedule_change_by_date_is_explicit_and_deterministic() -> None:
    windows = (
        _interval(
            "SBER", "2026-09-01 10:00:00", "2026-09-01 10:59:59",
            trade_date="2026-09-01", rule_version="old-hours-v1",
        ),
        _interval(
            "SBER", "2026-09-02 09:00:00", "2026-09-02 09:59:59",
            trade_date="2026-09-02", rule_version="new-hours-v2",
        ),
    )
    series = _series(
        SBER,
        "H1",
        (
            _candle("2026-09-01 10:00:00", "2026-09-01 10:59:59"),
            _candle("2026-09-02 09:00:00", "2026-09-02 09:59:59"),
        ),
    )
    evidence = _evidence(
        "SBER", "H1", windows, coverage_from="2026-09-01", coverage_till="2026-09-02"
    )
    first = audit_candle_sequence(
        instrument=SBER,
        timeframe="H1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 2),
        candle_series=series,
        schedule_evidence=evidence,
    )
    second = audit_candle_sequence(
        instrument=SBER,
        timeframe="H1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 2),
        candle_series=series,
        schedule_evidence=evidence,
    )

    assert first.status == "PASS"
    assert [item.rule_version for item in first.missing_intervals] == []
    assert first.to_dict() == second.to_dict()

def test_empty_successfully_read_contract_series_confirms_missing_bar() -> None:
    windows = (
        _interval("SBER", "2026-09-01 00:00:00", "2026-09-01 23:59:59", trade_date="2026-09-01"),
    )
    result = audit_candle_sequence(
        instrument=SBER,
        timeframe="D1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 1),
        candle_series=_series(SBER, "D1", ()),
        schedule_evidence=_evidence(
            "SBER", "D1", windows, coverage_from="2026-09-01", coverage_till="2026-09-01"
        ),
    )

    assert result.status == "DEGRADED"
    assert result.observed_count == 0
    assert len(result.missing_intervals) == 1
    assert result.unavailable_contracts == ()


def test_missing_rolled_contract_series_stays_unverified_not_a_gap() -> None:
    old = Instrument(
        symbol="SiU6", secid="SiU6", board="RFUD", engine="futures",
        market="forts", asset_class="future", root_symbol="Si",
    )
    windows = (
        _interval("SIU6", "2026-09-01 10:00:00", "2026-09-01 10:59:59", trade_date="2026-09-01"),
        _interval("SIZ6", "2026-09-02 10:00:00", "2026-09-02 10:59:59", trade_date="2026-09-02"),
    )
    result = audit_candle_sequence(
        instrument=SI_ROOT,
        timeframe="H1",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 2),
        candle_series=_series(
            old, "H1", (_candle("2026-09-01 10:00:00", "2026-09-01 10:59:59"),)
        ),
        schedule_evidence=_evidence(
            "SI", "H1", windows, coverage_from="2026-09-01", coverage_till="2026-09-02"
        ),
    )

    assert result.status == "UNVERIFIED"
    assert result.missing_intervals == ()
    assert result.unavailable_contracts == ("SIZ6",)
