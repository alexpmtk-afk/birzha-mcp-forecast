from datetime import date

from birzha.application.historical_data import _missing_session_ranges
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


def _instrument() -> Instrument:
    return Instrument(
        symbol="SBER", secid="SBER", board="TQBR", engine="stock", market="shares",
        asset_class="equity", name="Sberbank",
    )


def test_historical_store_is_idempotent_and_reports_coverage() -> None:
    store = DuckDBHistoricalCandleStore()
    instrument = _instrument()
    series = CandleSeries(
        instrument=instrument,
        timeframe="D1",
        candles=(
            Candle(100.0, 101.0, 102.0, 99.0, 1000.0, 10.0, "2026-09-01 00:00:00", "2026-09-01 23:59:59"),
            Candle(101.0, 103.0, 104.0, 100.0, 1200.0, 12.0, "2026-09-02 00:00:00", "2026-09-02 23:59:59"),
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
    assert store.stored_trade_dates("SBER", "D1", "2026-09-01", "2026-09-02") == (
        "2026-09-01", "2026-09-02"
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
    stored = ("2026-09-01", "2026-09-02", "2026-09-04", "2026-09-07", "2026-09-09")
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
