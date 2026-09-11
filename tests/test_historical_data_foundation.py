from datetime import date

import pytest

from birzha.application.historical_data import (
    D1_SESSION_VERIFICATION_VERSION,
    HistoricalDataIncompleteError,
    HistoricalDataService,
    ROLLING_HISTORY_VERIFICATION_VERSION,
    _bounded_missing_ranges,
    _missing_session_ranges,
)
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


def _instrument() -> Instrument:
    return Instrument(
        symbol="SBER",
        secid="SBER",
        board="TQBR",
        engine="stock",
        market="shares",
        asset_class="equity",
        name="Sberbank",
    )


def test_historical_store_is_idempotent_and_reports_coverage() -> None:
    store = DuckDBHistoricalCandleStore()
    instrument = _instrument()
    series = CandleSeries(
        instrument=instrument,
        timeframe="D1",
        candles=(
            Candle(
                100.0,
                101.0,
                102.0,
                99.0,
                1000.0,
                10.0,
                "2026-09-01 00:00:00",
                "2026-09-01 23:59:59",
            ),
            Candle(
                101.0,
                103.0,
                104.0,
                100.0,
                1200.0,
                12.0,
                "2026-09-02 00:00:00",
                "2026-09-02 23:59:59",
            ),
        ),
    )

    assert store.upsert_series(series) == 2
    assert store.upsert_series(series) == 2
    coverage = store.coverage("SBER", "D1")
    assert coverage.count == 2
    assert coverage.first_begin.startswith("2026-09-01")
    assert coverage.last_end.startswith("2026-09-02")
    loaded = store.read(instrument, "D1", "2026-09-01", "2026-09-02")
    assert loaded.count == 2
    assert store.stored_trade_dates(
        "SBER", "D1", "2026-09-01", "2026-09-02"
    ) == ("2026-09-01", "2026-09-02")
    store.record_sessions("SBER", "SBER", ("2026-09-01", "2026-09-02"))
    store.mark_session_range_verified("SBER", "2026-09-01", "2026-09-02")
    assert store.stored_sessions("SBER", "2026-09-01", "2026-09-02") == (
        "2026-09-01",
        "2026-09-02",
    )
    assert (
        store.is_session_range_verified("SBER", "2026-09-01", "2026-09-02")
        is True
    )


def test_missing_session_ranges_detect_internal_hole_without_weekend_guessing() -> None:
    expected = (
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
        date(2026, 9, 4),
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
    )
    stored = (
        "2026-09-01",
        "2026-09-02",
        "2026-09-04",
        "2026-09-07",
        "2026-09-09",
    )
    assert _missing_session_ranges(expected, stored) == (
        (date(2026, 9, 3), date(2026, 9, 3)),
        (date(2026, 9, 8), date(2026, 9, 8)),
    )


def test_missing_session_ranges_groups_missing_exchange_sessions() -> None:
    expected = (
        date(2026, 9, 4),
        date(2026, 9, 7),
        date(2026, 9, 8),
        date(2026, 9, 9),
    )
    assert _missing_session_ranges(expected, ("2026-09-04", "2026-09-09")) == (
        (date(2026, 9, 7), date(2026, 9, 8)),
    )


def test_verified_range_skips_market_access() -> None:
    store = DuckDBHistoricalCandleStore()
    store.mark_verified("SBER", "D1", "2026-09-01", "2026-09-05")
    store.mark_session_range_verified("SBER", "2026-09-01", "2026-09-05")
    service = HistoricalDataService(  # type: ignore[arg-type]
        market_data=object(), store=store
    )

    result = service.sync(
        "SBER", timeframe="D1", from_date="2026-09-02", till_date="2026-09-04"
    )

    assert result.reused_verified_range is True
    assert result.fetched_candles == 0
    assert result.contracts == ()


def test_sync_many_reuses_verified_ranges() -> None:
    store = DuckDBHistoricalCandleStore()
    for symbol in ("SBER", "IMOEX"):
        for timeframe in ("D1", "H1"):
            store.mark_verified(symbol, timeframe, "2026-09-01", "2026-09-05")
        store.mark_session_range_verified(symbol, "2026-09-01", "2026-09-05")
    service = HistoricalDataService(  # type: ignore[arg-type]
        market_data=object(), store=store
    )

    result = service.sync_many(
        ["SBER", "IMOEX"],
        ["D1", "H1"],
        from_date="2026-09-01",
        till_date="2026-09-05",
    )

    assert result["status"] == "PASS"
    assert result["requested"] == 4
    assert result["passed"] == 4
    assert all(item["reused_verified_range"] is True for item in result["items"])


def test_sync_many_continues_after_one_failure() -> None:
    store = DuckDBHistoricalCandleStore()
    store.mark_verified("SBER", "D1", "2026-09-01", "2026-09-05")
    store.mark_session_range_verified("SBER", "2026-09-01", "2026-09-05")
    service = HistoricalDataService(  # type: ignore[arg-type]
        market_data=object(), store=store
    )
    result = service.sync_many(
        ["SBER", "UNKNOWN"],
        ["D1"],
        from_date="2026-09-01",
        till_date="2026-09-05",
    )
    assert result["status"] == "PARTIAL"
    assert result["passed"] == 1
    assert result["failed"] == 1


def test_intraday_missing_sessions_are_bounded_into_small_fetches() -> None:
    expected = tuple(date(2026, 9, day) for day in range(1, 8))
    bounded = (
        (date(2026, 9, 1), date(2026, 9, 3)),
        (date(2026, 9, 4), date(2026, 9, 6)),
        (date(2026, 9, 7), date(2026, 9, 7)),
    )
    assert _bounded_missing_ranges(expected, (), "M15") == bounded
    assert _bounded_missing_ranges(expected, (), "H1") == bounded
    assert _bounded_missing_ranges(expected, (), "D1") == (
        (date(2026, 9, 1), date(2026, 9, 7)),
    )


class _FakeCalendar:
    def __init__(self, provider):
        pass

    def dates(self, **kwargs):
        return (date(2026, 9, 1), date(2026, 9, 2))


class _IncompleteMarketData:
    provider = object()

    def candles_for_instrument(
        self, instrument, *, timeframe, from_date, till_date, completed_only=True
    ):
        candle = Candle(
            100.0,
            101.0,
            102.0,
            99.0,
            1000.0,
            10.0,
            "2026-09-01 00:00:00",
            "2026-09-01 23:59:59",
        )
        return CandleSeries(
            instrument=instrument, timeframe=timeframe, candles=(candle,)
        )


def test_incomplete_sync_fails_closed(monkeypatch) -> None:
    import birzha.application.historical_data as module

    monkeypatch.setattr(module, "MoexTradingCalendar", _FakeCalendar)
    store = DuckDBHistoricalCandleStore()
    service = HistoricalDataService(  # type: ignore[arg-type]
        market_data=_IncompleteMarketData(), store=store
    )
    with pytest.raises(HistoricalDataIncompleteError):
        service._sync_contract(
            "SBER", _instrument(), "D1", date(2026, 9, 1), date(2026, 9, 2)
        )
    assert store.stored_trade_dates(
        "SBER", "D1", "2026-09-01", "2026-09-02"
    ) == ("2026-09-01",)


class _RootOnlyResolver:
    def resolve(self, symbol):
        return None


class _RootMarketData:
    direct_resolver = _RootOnlyResolver()


class _RootHistoryService(HistoricalDataService):
    def _segments(self, symbol, start, finish):
        return ()


def test_root_future_does_not_trust_stale_verified_range_and_fails_closed() -> None:
    store = DuckDBHistoricalCandleStore()
    store.mark_verified("Si", "D1", "2025-01-01", "2026-09-08")
    store.mark_session_range_verified("Si", "2025-01-01", "2026-09-08")
    service = _RootHistoryService(  # type: ignore[arg-type]
        market_data=_RootMarketData(), store=store
    )

    with pytest.raises(HistoricalDataIncompleteError, match="no segments"):
        service.sync(
            "Si", timeframe="D1", from_date="2025-01-01", till_date="2026-09-08"
        )

    current_key = (
        f"Si#{ROLLING_HISTORY_VERIFICATION_VERSION}#"
        f"{D1_SESSION_VERIFICATION_VERSION}"
    )
    assert store.is_verified(
        current_key, "D1", "2025-01-01", "2026-09-08"
    ) is False
