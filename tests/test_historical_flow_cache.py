from datetime import date

from birzha.application.historical_flow import (
    HistoricalFlowDataService,
    _bounded_date_ranges,
    _verification_dataset,
)
from birzha.domain.market import Instrument
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore


class FakeAnalytics:
    def __init__(self):
        self.trade_calls = 0
        self.futoi_calls = 0

    def fetch_tradestats(self, instrument, *, from_date, till_date):
        self.trade_calls += 1
        return [
            {
                "tradedate": from_date,
                "tradetime": "10:00:00",
                "seqnum": 1,
                "vol_b": 10,
                "vol_s": 5,
            }
        ]

    def fetch_futoi(self, instrument, *, from_date, till_date):
        self.futoi_calls += 1
        return [
            {
                "tradedate": from_date,
                "tradetime": "10:00:00",
                "clgroup": "FIZ",
                "pos": 100,
            }
        ]


class FakeMarketData:
    direct_resolver = None
    historical_future_resolver = None


FUT = Instrument(
    symbol="Si",
    secid="SiU6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="Si",
)
INDEX = Instrument(
    symbol="IMOEX",
    secid="IMOEX",
    board="SNDX",
    engine="stock",
    market="index",
    asset_class="index",
)


def test_repeated_tradestats_range_uses_store_without_second_upstream_call():
    analytics = FakeAnalytics()
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=FakeMarketData(), analytics=analytics, store=store
    )
    first = service.tradestats(FUT, from_date="2026-09-01", till_date="2026-09-02")
    second = service.tradestats(FUT, from_date="2026-09-01", till_date="2026-09-02")
    assert first == second
    assert analytics.trade_calls == 1
    store.close()


def test_repeated_futoi_range_uses_store_without_second_upstream_call():
    analytics = FakeAnalytics()
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=FakeMarketData(), analytics=analytics, store=store
    )
    first = service.futoi(FUT, from_date="2026-09-01", till_date="2026-09-02")
    second = service.futoi(FUT, from_date="2026-09-01", till_date="2026-09-02")
    assert first == second
    assert analytics.futoi_calls == 1
    store.close()


def test_legacy_futoi_marker_does_not_hide_current_flow_semantics():
    analytics = FakeAnalytics()
    store = DuckDBHistoricalFlowStore(":memory:")
    store.mark_verified("FUTOI", "Si", "2026-09-01", "2026-09-02")
    service = HistoricalFlowDataService(
        market_data=FakeMarketData(), analytics=analytics, store=store
    )

    rows = service.futoi(FUT, from_date="2026-09-01", till_date="2026-09-02")

    assert len(rows) == 1
    assert analytics.futoi_calls == 1
    assert store.is_verified(
        _verification_dataset("FUTOI"), "Si", "2026-09-01", "2026-09-02"
    ) is True
    store.close()


def test_unsupported_index_tradestats_degrades_without_upstream_call():
    analytics = FakeAnalytics()
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=FakeMarketData(), analytics=analytics, store=store
    )

    rows = service.tradestats(
        INDEX, from_date="2026-09-01", till_date="2026-09-02"
    )

    assert rows == []
    assert analytics.trade_calls == 0
    store.close()


def test_long_flow_ranges_are_split_into_safe_calendar_windows():
    ranges = _bounded_date_ranges(date(2026, 1, 1), date(2026, 5, 1))
    assert ranges == (
        (date(2026, 1, 1), date(2026, 3, 1)),
        (date(2026, 3, 2), date(2026, 4, 30)),
        (date(2026, 5, 1), date(2026, 5, 1)),
    )


def test_bounded_futoi_marks_whole_range_after_all_chunks():
    analytics = FakeAnalytics()
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=FakeMarketData(), analytics=analytics, store=store
    )

    rows = service._futoi_bounded(FUT, date(2026, 1, 1), date(2026, 5, 1))

    assert analytics.futoi_calls == 3
    assert len(rows) == 3
    assert store.is_verified(
        _verification_dataset("FUTOI"), "Si", "2026-01-01", "2026-05-01"
    ) is True
    store.close()
