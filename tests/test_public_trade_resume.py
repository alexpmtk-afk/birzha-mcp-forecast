from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from birzha.application.historical_flow import (
    HistoricalFlowDataService,
    PUBLIC_TRADES_RAW_DATASET,
    PUBLIC_TRADES_RAW_SOURCE,
)
from birzha.application.public_tradestats import PUBLIC_LATEST_TRADESTATS_SOURCE
from birzha.domain.market import Instrument
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore


INSTRUMENT = Instrument(
    symbol="BR",
    secid="BRV6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="BR",
)


def _trade(recno: int, tradetime: str, side: str, quantity: int, price: float):
    return {
        "RECNO": recno,
        "TRADENO": 1000 + recno,
        "TRADEDATE": datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat(),
        "TRADETIME": tradetime,
        "PRICE": price,
        "QUANTITY": quantity,
        "OPENPOSITION": 100 + recno,
        "BUYSELL": side,
        "OFFMARKETDEAL": 0,
    }


class _Market:
    def resolve(self, symbol: str):
        assert symbol == "BR"
        return INSTRUMENT


class _FailAfterFirstPage:
    def __init__(self):
        self.starts: list[int] = []

    def fetch_public_recent_trade_page(self, instrument, *, start=0):
        assert instrument.secid == "BRV6"
        self.starts.append(start)
        if start == 0:
            return [
                _trade(1, "10:00:01", "B", 4, 106.10),
                _trade(2, "10:02:00", "S", 3, 106.20),
            ], 2, False
        raise RuntimeError("simulated timeout")


class _ResumeFromSecondPage:
    def __init__(self):
        self.starts: list[int] = []

    def fetch_public_recent_trade_page(self, instrument, *, start=0):
        assert instrument.secid == "BRV6"
        self.starts.append(start)
        assert start == 2
        return [
            _trade(3, "10:04:59", "B", 2, 106.30),
        ], 3, True


def test_public_trade_capture_resumes_after_failed_page() -> None:
    store = DuckDBHistoricalFlowStore(":memory:")
    first = _FailAfterFirstPage()
    service = HistoricalFlowDataService(
        market_data=_Market(),
        analytics=first,
        store=store,
    )

    with pytest.raises(RuntimeError, match="simulated timeout"):
        service.capture_public_recent_tradestats("BR")

    assert first.starts == [0, 2]

    second = _ResumeFromSecondPage()
    resumed = HistoricalFlowDataService(
        market_data=_Market(),
        analytics=second,
        store=store,
    )
    result = resumed.capture_public_recent_tradestats("BR")

    assert second.starts == [2]
    assert result["resume_start"] == 2
    assert result["next_start"] == 3
    assert result["raw_trades_fetched"] == 1
    assert result["raw_trades_total"] == 3
    assert result["derived_5m_rows"] == 1

    rows = store.read_rows(
        "TRADESTATS",
        "BRV6",
        datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat(),
        datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat(),
    )
    assert len(rows) == 1
    assert rows[0]["pr_open"] == 106.10
    assert rows[0]["pr_close"] == 106.30
    assert rows[0]["vol_b"] == 6.0
    assert rows[0]["vol_s"] == 3.0


def test_raw_public_trade_replay_does_not_duplicate_rows() -> None:
    store = DuckDBHistoricalFlowStore(":memory:")
    day = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    rows = [
        _trade(1, "10:00:01", "B", 4, 106.10),
        _trade(2, "10:00:01", "S", 3, 106.20),
    ]

    store.upsert_rows(PUBLIC_TRADES_RAW_DATASET, "BRV6", rows, PUBLIC_TRADES_RAW_SOURCE)
    store.upsert_rows(PUBLIC_TRADES_RAW_DATASET, "BRV6", rows, PUBLIC_TRADES_RAW_SOURCE)

    stored = store.read_rows(PUBLIC_TRADES_RAW_DATASET, "BRV6", day, day)
    assert len(stored) == 2
    assert {row["RECNO"] for row in stored} == {1, 2}


class _ThreePageAnalytics:
    def __init__(self):
        self.starts: list[int] = []

    def fetch_public_recent_trade_page(self, instrument, *, start=0):
        assert instrument.secid == "BRV6"
        self.starts.append(start)
        if start == 0:
            return [
                _trade(10, "11:00:01", "B", 1, 107.10),
                _trade(11, "11:01:01", "S", 2, 107.20),
            ], 2, False
        if start == 2:
            return [
                _trade(12, "11:02:01", "B", 3, 107.30),
                _trade(13, "11:03:01", "S", 4, 107.40),
            ], 4, False
        if start == 4:
            return [
                _trade(14, "11:04:01", "B", 5, 107.50),
            ], 5, True
        raise AssertionError(f"unexpected start={start}")


def test_public_trade_capture_finishes_in_short_checkpointed_workers() -> None:
    store = DuckDBHistoricalFlowStore(":memory:")
    analytics = _ThreePageAnalytics()
    service = HistoricalFlowDataService(
        market_data=_Market(),
        analytics=analytics,
        store=store,
    )

    first = service.capture_public_recent_tradestats("BR")

    assert first["complete"] is False
    assert first["resume_start"] == 0
    assert first["next_start"] == 4
    assert first["pages_fetched"] == 2
    assert first["raw_trades_fetched"] == 4
    assert first["raw_trades_total"] == 4
    assert first["derived_5m_rows"] == 1
    assert first["rows_written"] == 1
    assert analytics.starts == [0, 2]

    second = service.capture_public_recent_tradestats("BR")

    assert second["complete"] is True
    assert second["resume_start"] == 4
    assert second["next_start"] == 5
    assert second["pages_fetched"] == 1
    assert second["raw_trades_fetched"] == 1
    assert second["raw_trades_total"] == 5
    assert second["derived_5m_rows"] == 1
    assert second["rows_written"] == 1
    assert analytics.starts == [0, 2, 4]


EQUITY_INSTRUMENT = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
    root_symbol="SBER",
)


class _EquityMarket:
    def resolve(self, symbol: str):
        assert symbol == "SBER"
        return EQUITY_INSTRUMENT


class _EquityOnePageAnalytics:
    def fetch_public_recent_trade_page(self, instrument, *, start=0):
        assert instrument is EQUITY_INSTRUMENT
        assert start == 0
        day = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        return [
            {
                "TRADENO": 1,
                "TRADEDATE": day,
                "TRADETIME": "10:00:01",
                "PRICE": 315.5,
                "QUANTITY": 10,
                "VALUE": 3155.0,
                "BUYSELL": "B",
            },
            {
                "TRADENO": 2,
                "TRADEDATE": day,
                "TRADETIME": "10:01:01",
                "PRICE": 315.6,
                "QUANTITY": 5,
                "VALUE": 1578.0,
                "BUYSELL": "S",
            },
        ], 2, True


def test_public_equity_capture_derives_trade_stats_without_futoi() -> None:
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=_EquityMarket(),
        analytics=_EquityOnePageAnalytics(),
        store=store,
    )

    result = service.capture_public_recent_tradestats("SBER")

    assert result["complete"] is True
    assert result["raw_trades_total"] == 2
    assert result["derived_5m_rows"] == 1
    day = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    rows = store.read_rows("TRADESTATS", "SBER", day, day)
    assert len(rows) == 1
    assert rows[0]["vol_b"] == 10.0
    assert rows[0]["vol_s"] == 5.0
    assert rows[0]["val_b"] == 3155.0
    assert rows[0]["val_s"] == 1578.0
    assert rows[0]["oi_open"] is None
    assert rows[0]["oi_close"] is None


GOLD_INSTRUMENT = Instrument(
    symbol="GOLD",
    secid="GDZ6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="GOLD",
)


class _GoldMarket:
    def resolve(self, symbol: str):
        assert symbol == "GOLD"
        return GOLD_INSTRUMENT


class _GoldLatestAnalytics:
    authenticated = False

    def __init__(self):
        self.latest_calls: list[tuple[str, str, bool]] = []

    def fetch_tradestats(self, instrument, *, from_date, till_date, latest=False):
        assert instrument is GOLD_INSTRUMENT
        self.latest_calls.append((from_date, till_date, latest))
        return [
            {
                "tradedate": from_date,
                "tradetime": "10:05:00",
                "pr_open": 4200.0,
                "pr_close": 4201.0,
                "vol_b": 12.0,
                "vol_s": 8.0,
                "oi_open": 1000.0,
                "oi_close": 1010.0,
            },
            {
                "tradedate": from_date,
                "tradetime": "10:10:00",
                "pr_open": 4201.0,
                "pr_close": 4202.0,
                "vol_b": 10.0,
                "vol_s": 9.0,
                "oi_open": 1010.0,
                "oi_close": 1012.0,
            },
        ]

    def fetch_public_recent_trade_page(self, *args, **kwargs):
        raise AssertionError("raw public trades fallback must not run when latest TradeStats is available")


def test_gold_prefers_free_latest_tradestats_before_raw_trades() -> None:
    store = DuckDBHistoricalFlowStore(":memory:")
    analytics = _GoldLatestAnalytics()
    service = HistoricalFlowDataService(
        market_data=_GoldMarket(),
        analytics=analytics,
        store=store,
    )

    result = service.capture_public_recent_tradestats("GOLD")

    assert result["complete"] is True
    assert result["capture_mode"] == "PUBLIC_ISS_LATEST_TRADESTATS"
    assert result["source"] == PUBLIC_LATEST_TRADESTATS_SOURCE
    assert result["derived_5m_rows"] == 2
    assert result["rows_written"] == 2
    assert len(analytics.latest_calls) == 1
    assert analytics.latest_calls[0][2] is True

    day = result["capture_date"]
    rows = store.read_rows("TRADESTATS", "GDZ6", day, day)
    assert len(rows) == 2
    assert {row["_source"] for row in rows} == {PUBLIC_LATEST_TRADESTATS_SOURCE}


class _GoldLatestEmptyThenRaw:
    authenticated = False

    def fetch_tradestats(self, instrument, *, from_date, till_date, latest=False):
        assert instrument is GOLD_INSTRUMENT
        assert latest is True
        return []

    def fetch_public_recent_trade_page(self, instrument, *, start=0):
        assert instrument is GOLD_INSTRUMENT
        day = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        return [
            {
                "RECNO": 1,
                "TRADENO": 1001,
                "TRADEDATE": day,
                "TRADETIME": "10:00:01",
                "PRICE": 4200.0,
                "QUANTITY": 2,
                "OPENPOSITION": 1000,
                "BUYSELL": "B",
                "OFFMARKETDEAL": 0,
            }
        ], 1, True


def test_gold_falls_back_to_raw_public_trades_when_latest_is_empty() -> None:
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=_GoldMarket(),
        analytics=_GoldLatestEmptyThenRaw(),
        store=store,
    )

    result = service.capture_public_recent_tradestats("GOLD")

    assert result["complete"] is True
    assert "capture_mode" not in result
    assert result["raw_trades_total"] == 1
    assert result["derived_5m_rows"] == 1
