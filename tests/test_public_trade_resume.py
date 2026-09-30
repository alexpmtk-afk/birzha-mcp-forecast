from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from birzha.application.historical_flow import (
    HistoricalFlowDataService,
    PUBLIC_TRADES_RAW_DATASET,
    PUBLIC_TRADES_RAW_SOURCE,
)
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
