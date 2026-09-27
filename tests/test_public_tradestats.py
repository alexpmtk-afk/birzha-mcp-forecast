from __future__ import annotations

from birzha.application.public_tradestats import (
    PUBLIC_TRADESTATS_SOURCE,
    aggregate_public_futures_trades,
)


def test_public_trades_are_aggregated_into_five_minute_rows() -> None:
    rows = [
        {"TRADEDATE":"2026-09-26","TRADETIME":"10:00:01","PRICE":106.10,"QUANTITY":4,"OPENPOSITION":100,"BUYSELL":"B","RECNO":1,"TRADENO":11,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-09-26","TRADETIME":"10:02:00","PRICE":106.20,"QUANTITY":3,"OPENPOSITION":104,"BUYSELL":"S","RECNO":2,"TRADENO":12,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-09-26","TRADETIME":"10:04:59","PRICE":106.30,"QUANTITY":2,"OPENPOSITION":106,"BUYSELL":"B","RECNO":3,"TRADENO":13,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-09-26","TRADETIME":"10:05:00","PRICE":106.40,"QUANTITY":5,"OPENPOSITION":108,"BUYSELL":"S","RECNO":4,"TRADENO":14,"OFFMARKETDEAL":0},
        {"TRADEDATE":"2026-09-26","TRADETIME":"10:05:30","PRICE":999.00,"QUANTITY":99,"OPENPOSITION":999,"BUYSELL":"B","RECNO":5,"TRADENO":15,"OFFMARKETDEAL":1},
    ]

    result = aggregate_public_futures_trades(rows)

    assert len(result) == 2
    first, second = result
    assert first["tradedate"] == "2026-09-26"
    assert first["tradetime"] == "10:00:00"
    assert first["pr_open"] == 106.10
    assert first["pr_close"] == 106.30
    assert first["vol_b"] == 6.0
    assert first["vol_s"] == 3.0
    assert first["val_b"] is None
    assert first["val_s"] is None
    assert first["oi_open"] == 100.0
    assert first["oi_close"] == 106.0
    assert first["trades_b"] == 2
    assert first["trades_s"] == 1
    assert first["_source"] == PUBLIC_TRADESTATS_SOURCE

    assert second["tradetime"] == "10:05:00"
    assert second["pr_open"] == 106.40
    assert second["pr_close"] == 106.40
    assert second["vol_b"] == 0
    assert second["vol_s"] == 5.0
    assert second["oi_open"] == 108.0
    assert second["oi_close"] == 108.0


def test_invalid_or_incomplete_public_trade_rows_are_ignored() -> None:
    rows = [
        {"TRADEDATE":"2026-09-26","TRADETIME":"10:00:00","PRICE":100,"QUANTITY":1,"BUYSELL":"X"},
        {"TRADEDATE":"2026-09-26","TRADETIME":"bad","PRICE":100,"QUANTITY":1,"BUYSELL":"B"},
        {"TRADEDATE":"2026-09-26","TRADETIME":"10:00:00","PRICE":None,"QUANTITY":1,"BUYSELL":"B"},
    ]
    assert aggregate_public_futures_trades(rows) == []
