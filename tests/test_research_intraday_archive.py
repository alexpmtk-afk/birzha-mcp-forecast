from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.research_intraday_fetch import (
    aggregate_strict_m15, prepare_one_day,
)
from birzha.domain.market import Candle, CandleSeries, Instrument


def instrument():
    return Instrument(
        symbol="SBER", secid="SBER", board="TQBR",
        engine="stock", market="shares", asset_class="equity",
    )


def bar(start, seconds, value=100.0):
    end = start + timedelta(seconds=seconds)
    return Candle(
        open=value, close=value + 1, high=value + 2, low=value - 1,
        volume=10., value=1000., begin=start.isoformat(),
        end=end.isoformat(), completed=True,
    )


def hourly():
    t = datetime.fromisoformat("2025-06-09T10:00:00+03:00")
    return CandleSeries(instrument(), "H1", (bar(t, 3599),))


def minute_rows():
    t = datetime.fromisoformat("2025-06-09T10:00:00+03:00")
    return CandleSeries(
        instrument(), "M1",
        tuple(bar(t + timedelta(minutes=i), 59, value=100+i) for i in range(15))
    )


def test_derived_15_minute_bar_uses_exact_15_complete_minutes():
    m15, incomplete = aggregate_strict_m15(minute_rows(), "2025-06-09")
    assert m15.count == 1 and incomplete == 0
    candle = m15.candles[0]
    assert candle.open == 100 and candle.close == 115
    assert candle.high == 116 and candle.low == 99
    assert candle.volume == 150
    assert candle.end.endswith("10:14:59+03:00")
    assert candle.available_at_confidence == "INFERRED"


def test_missing_minute_or_duplicate_minute_never_yields_fake_complete_m15():
    original = minute_rows()
    for candles in (
        original.candles[:-1],
        original.candles[:3] + (original.candles[2],) + original.candles[3:],
    ):
        result, incomplete = aggregate_strict_m15(
            replace(original, candles=candles), "2025-06-09"
        )
        assert result.count == 0 and incomplete >= 1


def test_incomplete_and_out_of_bounds_m15_are_fail_closed():
    original = minute_rows()
    partial = replace(original.candles[0], completed=False)
    result, incomplete = aggregate_strict_m15(
        replace(original, candles=(partial,)+original.candles[1:]), "2025-06-09"
    )
    assert result.count == 0 and incomplete >= 1
    with pytest.raises(ValueError, match="2025-01-01..2026-12-31"):
        aggregate_strict_m15(original, "2024-12-31")


class FakeMarket:
    def __init__(self, *, gap=False):
        self.calls = []
        self.gap = gap

    def candles_for_instrument(self, instrument, *, timeframe, from_date,
                               till_date, completed_only):
        self.calls.append((timeframe, from_date, till_date))
        if timeframe == "H1":
            return hourly()
        assert timeframe == "M1"
        source = minute_rows()
        return replace(source, candles=source.candles[:-1] if self.gap
                       else source.candles)


def test_one_day_fetch_reads_only_native_h1_m1_and_skips_unproven_m15():
    market = FakeMarket(gap=True)
    result = prepare_one_day(
        market, instrument(), "2025-06-09", current_date="2026-10-13"
    )
    assert market.calls == [
        ("H1", "2025-06-09", "2025-06-09"),
        ("M1", "2025-06-09", "2025-06-09"),
    ]
    assert result.hourly.count == 1
    assert result.quarter_hour.count == 0
    assert result.incomplete_m15_buckets == 1


def test_no_network_for_forbidden_or_current_dates():
    market = FakeMarket()
    for date in ("2024-01-01", "2027-01-01"):
        with pytest.raises(ValueError, match="2025-01-01..2026-12-31"):
            prepare_one_day(market, instrument(), date, current_date="2026-10-13")
    with pytest.raises(ValueError, match="completed past"):
        prepare_one_day(market, instrument(), "2026-10-13", current_date="2026-10-13")
    assert market.calls == []


def test_research_duckdb_idempotent_and_read_exact(tmp_path):
    path = tmp_path/"isolated_research.duckdb"
    store = ResearchIntradayArchive(str(path))
    try:
        item = hourly()
        assert store.ingest(item)["inserted"] == 1
        assert store.ingest(item)["reused"] == 1
        assert store.read_exact(
            instrument(), timeframe="H1",
            from_date="2025-06-09", till_date="2025-06-09",
        ).count == 1
        assert store.inventory()[0]["rows"] == 1
        assert store.ingest(aggregate_strict_m15(minute_rows(), "2025-06-09")[0])["inserted"] == 1
        assert store.read_exact(
            instrument(), timeframe="M15",
            from_date="2025-06-09", till_date="2025-06-09",
        ).count == 1
    finally:
        store.close()
    # The marked archive is resumable.
    reopened = ResearchIntradayArchive(str(path))
    try:
        assert reopened.ingest(hourly())["reused"] == 1
        assert len(reopened.inventory()) == 2
    finally:
        reopened.close()


def test_conflicting_immutable_batch_rolls_back_all_insertions(tmp_path):
    store = ResearchIntradayArchive(str(tmp_path/"research.duckdb"))
    try:
        initial = hourly()
        store.ingest(initial)
        first = initial.candles[0]
        new = replace(first, begin="2025-06-09T09:00:00+03:00",
                      end="2025-06-09T09:59:59+03:00")
        changed = replace(first, close=100.5)
        with pytest.raises(ValueError, match="conflicting"):
            store.ingest(replace(initial, candles=(new, changed)))
        assert store.inventory()[0]["rows"] == 1
    finally:
        store.close()


def test_preexisting_unmarked_duckdb_is_never_modified(tmp_path):
    path = tmp_path/"main_like.duckdb"
    cx = duckdb.connect(str(path))
    cx.execute("CREATE TABLE important_data (x INTEGER)")
    cx.execute("INSERT INTO important_data VALUES (42)")
    cx.close()
    with pytest.raises(ValueError, match="without research archive marker"):
        ResearchIntradayArchive(str(path))
    cx = duckdb.connect(str(path), read_only=True)
    try:
        assert cx.execute("SELECT * FROM important_data").fetchall() == [(42,)]
    finally:
        cx.close()


@pytest.mark.parametrize("tf", ["H1", "M15"])
def test_archive_rejects_2024_2027_and_d1(tmp_path, tf):
    store = ResearchIntradayArchive(str(tmp_path/"research.duckdb"))
    try:
        for year in ("2024", "2027"):
            candle = bar(datetime.fromisoformat(f"{year}-06-09T10:00:00+03:00"),
                         3599 if tf == "H1" else 899)
            with pytest.raises(ValueError, match="2025-01-01..2026-12-31"):
                store.ingest(CandleSeries(instrument(), tf, (candle,)))
        with pytest.raises(ValueError, match="only H1 and M15"):
            store.ingest(CandleSeries(instrument(), "D1", hourly().candles))
    finally:
        store.close()
