from __future__ import annotations

from datetime import date

import pytest

from birzha.application.flow import _latest_session_vwap
from birzha.application.historical_flow import (
    HistoricalFlowDataService,
    PUBLIC_TRADES_RAW_DATASET,
    PUBLIC_TRADES_RAW_SOURCE,
)
from birzha.domain.market import Instrument
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore


INSTRUMENT = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
)


def test_session_vwap_uses_latest_trade_date_and_excludes_offmarket() -> None:
    raw = [
        {"TRADEDATE":"2026-10-01","TRADETIME":"18:00:00","PRICE":100.0,"QUANTITY":100,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-10-02","TRADETIME":"10:00:00","PRICE":110.0,"QUANTITY":2,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-10-02","TRADETIME":"10:01:00","PRICE":120.0,"QUANTITY":1,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-10-02","TRADETIME":"10:02:00","PRICE":999.0,"QUANTITY":100,"OFFMARKETDEAL":1},
    ]

    value, source = _latest_session_vwap(raw, [])

    assert value == pytest.approx((110.0 * 2 + 120.0) / 3)
    assert source == "PUBLIC_TRADES_PRICE_QUANTITY"


def test_session_vwap_can_use_explicit_tradestats_vwap_when_raw_is_absent() -> None:
    rows = [
        {"tradedate":"2026-10-02","tradetime":"10:00:00","pr_vwap":100.0,"vol_b":2,"vol_s":2},
        {"tradedate":"2026-10-02","tradetime":"10:05:00","pr_vwap":110.0,"vol_b":4,"vol_s":2},
    ]

    value, source = _latest_session_vwap([], rows)

    assert value == pytest.approx((100.0 * 4 + 110.0 * 6) / 10)
    assert source == "TRADESTATS_VWAP_WEIGHTED"


def test_raw_public_trades_causal_read_stops_at_t0() -> None:
    store = DuckDBHistoricalFlowStore(":memory:")
    store.upsert_rows(
        PUBLIC_TRADES_RAW_DATASET,
        "SBER",
        [
            {"TRADEDATE":"2026-10-02","TRADETIME":"10:00:00","TRADENO":1,"PRICE":100.0,"QUANTITY":1},
            {"TRADEDATE":"2026-10-02","TRADETIME":"10:10:00","TRADENO":2,"PRICE":200.0,"QUANTITY":1},
        ],
        PUBLIC_TRADES_RAW_SOURCE,
    )
    service = HistoricalFlowDataService(
        market_data=None,  # type: ignore[arg-type]
        analytics=None,  # type: ignore[arg-type]
        store=store,
    )

    rows = service.public_trades_causal(
        INSTRUMENT,
        from_date="2026-10-02",
        till_date="2026-10-02",
        cutoff_at="2026-10-02T10:05:00+03:00",
    )

    assert len(rows) == 1
    assert rows[0]["TRADENO"] == 1
    value, _ = _latest_session_vwap(rows, [])
    assert value == 100.0
    store.close()
