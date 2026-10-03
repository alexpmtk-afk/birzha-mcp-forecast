from datetime import date

import pytest

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


def test_read_only_flow_never_fetches_unverified_optional_data():
    analytics = FakeAnalytics()
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=FakeMarketData(),
        analytics=analytics,
        store=store,
        read_only=True,
    )

    assert service.tradestats(
        FUT, from_date="2026-09-01", till_date="2026-09-02"
    ) == []
    assert service.futoi(
        FUT, from_date="2026-09-01", till_date="2026-09-02"
    ) == []
    assert analytics.trade_calls == 0
    assert analytics.futoi_calls == 0
    with pytest.raises(RuntimeError, match="read-only historical flow service cannot sync"):
        service.sync("Si", from_date="2026-09-01", till_date="2026-09-02")
    store.close()


def test_read_only_flow_reads_only_current_verified_cache():
    analytics = FakeAnalytics()
    store = DuckDBHistoricalFlowStore(":memory:")
    rows = [
        {
            "tradedate": "2026-09-01",
            "tradetime": "10:00:00",
            "seqnum": 1,
            "vol_b": 10,
            "vol_s": 5,
        }
    ]
    store.upsert_rows("TRADESTATS", "SiU6", rows, "TEST")
    store.mark_verified(
        _verification_dataset("TRADESTATS"),
        "SiU6",
        "2026-09-01",
        "2026-09-02",
    )
    service = HistoricalFlowDataService(
        market_data=FakeMarketData(),
        analytics=analytics,
        store=store,
        read_only=True,
    )

    assert service.tradestats(
        FUT, from_date="2026-09-01", till_date="2026-09-02"
    ) == rows
    assert analytics.trade_calls == 0
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



def test_causal_tradestats_read_uses_availability_time():
    store = DuckDBHistoricalFlowStore(":memory:")
    rows = [{
        "tradedate": "2026-09-01",
        "tradetime": "10:00:00",
        "seqnum": 1,
        "vol_b": 10,
        "vol_s": 5,
    }]
    store.upsert_rows("TRADESTATS", "SiU6", rows, "MOEX_ALGOPACK")

    before = store.read_rows_causal(
        "TRADESTATS",
        "SiU6",
        "2026-09-01",
        "2026-09-01",
        "2026-09-01T09:59:59+03:00",
    )
    after = store.read_rows_causal(
        "TRADESTATS",
        "SiU6",
        "2026-09-01",
        "2026-09-01",
        "2026-09-01T10:00:00+03:00",
    )

    assert before == []
    assert after == rows
    store.close()


def test_causal_futoi_without_proven_availability_is_excluded():
    store = DuckDBHistoricalFlowStore(":memory:")
    rows = [{
        "tradedate": "2026-09-01",
        "tradetime": "10:00:00",
        "clgroup": "FIZ",
        "pos": 100,
    }]
    store.upsert_rows("FUTOI", "Si", rows, "MOEX_FUTOI")

    causal = store.read_rows_causal(
        "FUTOI",
        "Si",
        "2026-09-01",
        "2026-09-01",
        "2026-09-01T23:59:59+03:00",
    )

    assert causal == []
    store.close()


def test_causal_futoi_with_documented_publish_time_is_allowed():
    store = DuckDBHistoricalFlowStore(":memory:")
    rows = [{
        "tradedate": "2026-09-01",
        "tradetime": "10:00:00",
        "systime": "2026-09-01T10:02:00+03:00",
        "clgroup": "FIZ",
        "pos": 100,
    }]
    store.upsert_rows("FUTOI", "Si", rows, "MOEX_FUTOI")

    before = store.read_rows_causal(
        "FUTOI",
        "Si",
        "2026-09-01",
        "2026-09-01",
        "2026-09-01T10:01:59+03:00",
    )
    after = store.read_rows_causal(
        "FUTOI",
        "Si",
        "2026-09-01",
        "2026-09-01",
        "2026-09-01T10:02:00+03:00",
    )

    assert before == []
    assert after == rows
    store.close()



def test_flow_first_seen_payload_is_immutable_and_revision_is_recorded():
    store = DuckDBHistoricalFlowStore(":memory:")
    first = [{
        "tradedate": "2026-09-01",
        "tradetime": "10:00:00",
        "seqnum": 1,
        "vol_b": 10,
        "vol_s": 5,
    }]
    revised = [{
        "tradedate": "2026-09-01",
        "tradetime": "10:00:00",
        "seqnum": 1,
        "vol_b": 999,
        "vol_s": 5,
    }]

    store.upsert_rows("TRADESTATS", "SiU6", first, "MOEX_ALGOPACK")
    store.upsert_rows("TRADESTATS", "SiU6", revised, "MOEX_ALGOPACK")

    loaded = store.read_rows(
        "TRADESTATS", "SiU6", "2026-09-01", "2026-09-01"
    )
    assert loaded == first
    revision_count = store._connection.execute(
        "SELECT count(*) FROM historical_flow_revisions"
    ).fetchone()[0]
    assert revision_count == 1
    store.close()
