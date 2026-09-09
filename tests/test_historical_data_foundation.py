from datetime import date

from birzha.application.historical_data import _missing_tail_ranges
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore, HistoricalCoverage


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


def test_missing_ranges_fetch_only_uncovered_tails() -> None:
    coverage = HistoricalCoverage(
        secid="SBER",
        timeframe="D1",
        first_begin="2026-09-03 00:00:00",
        last_end="2026-09-07 23:59:59",
        count=3,
    )
    assert _missing_tail_ranges(date(2026, 9, 1), date(2026, 9, 9), coverage) == (
        (date(2026, 9, 1), date(2026, 9, 2)),
        (date(2026, 9, 8), date(2026, 9, 9)),
    )


def test_empty_coverage_requests_full_range() -> None:
    coverage = HistoricalCoverage("SBER", "D1", None, None, 0)
    assert _missing_tail_ranges(date(2026, 9, 1), date(2026, 9, 9), coverage) == (
        (date(2026, 9, 1), date(2026, 9, 9)),
    )
