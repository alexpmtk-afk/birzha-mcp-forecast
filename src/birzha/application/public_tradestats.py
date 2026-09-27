"""Derive TradeStats-compatible five-minute rows from public MOEX futures trades.

The public futures trades feed exposes side, price, quantity and open interest
for the current trading day. It does not expose TradeStats RUB value fields,
so val_b/val_s intentionally remain NULL instead of being approximated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


PUBLIC_TRADESTATS_SOURCE = "MOEX_ISS_PUBLIC_TRADES_DERIVED"


def aggregate_public_futures_trades(
    rows: list[dict[str, object]],
    *,
    interval_minutes: int = 5,
) -> list[dict[str, object]]:
    if interval_minutes <= 0 or 60 % interval_minutes != 0:
        raise ValueError("interval_minutes must be a positive divisor of 60")

    buckets: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        if _int(row, "OFFMARKETDEAL", "offmarketdeal") not in {None, 0}:
            continue
        tradedate = _text(row, "TRADEDATE", "tradedate")
        tradetime = _text(row, "TRADETIME", "tradetime")
        side = _text(row, "BUYSELL", "buysell").upper()
        price = _float(row, "PRICE", "price")
        quantity = _float(row, "QUANTITY", "quantity")
        if not tradedate or not tradetime or side not in {"B", "S"}:
            continue
        if price is None or quantity is None:
            continue
        try:
            dt = datetime.fromisoformat(f"{tradedate[:10]}T{tradetime}")
        except ValueError:
            continue
        minute = (dt.minute // interval_minutes) * interval_minutes
        bucket_time = f"{dt.hour:02d}:{minute:02d}:00"
        buckets.setdefault((tradedate[:10], bucket_time), []).append(row)

    result: list[dict[str, object]] = []
    for (tradedate, bucket_time), group in sorted(buckets.items()):
        ordered = sorted(group, key=_trade_sort_key)
        buy = [row for row in ordered if _text(row, "BUYSELL", "buysell").upper() == "B"]
        sell = [row for row in ordered if _text(row, "BUYSELL", "buysell").upper() == "S"]
        first_price = _float(ordered[0], "PRICE", "price")
        last_price = _float(ordered[-1], "PRICE", "price")
        first_oi = _first_number(ordered, "OPENPOSITION", "openposition")
        last_oi = _last_number(ordered, "OPENPOSITION", "openposition")

        result.append(
            {
                "tradedate": tradedate,
                "tradetime": bucket_time,
                "pr_open": first_price,
                "pr_close": last_price,
                "vol_b": _sum_quantity(buy),
                "vol_s": _sum_quantity(sell),
                "val_b": None,
                "val_s": None,
                "oi_open": first_oi,
                "oi_close": last_oi,
                "trades_b": len(buy),
                "trades_s": len(sell),
                "first_recno": _int(ordered[0], "RECNO", "recno"),
                "last_recno": _int(ordered[-1], "RECNO", "recno"),
                "_source": PUBLIC_TRADESTATS_SOURCE,
                "_value_fields": "UNAVAILABLE_IN_PUBLIC_FUTURES_TRADES",
            }
        )
    return result


def _trade_sort_key(row: dict[str, object]) -> tuple[str, str, int, int]:
    return (
        _text(row, "TRADEDATE", "tradedate"),
        _text(row, "TRADETIME", "tradetime"),
        _int(row, "RECNO", "recno") or 0,
        _int(row, "TRADENO", "tradeno") or 0,
    )


def _sum_quantity(rows: list[dict[str, object]]) -> float:
    return sum(value for row in rows if (value := _float(row, "QUANTITY", "quantity")) is not None)


def _first_number(rows: list[dict[str, object]], *keys: str) -> float | None:
    for row in rows:
        value = _float(row, *keys)
        if value is not None:
            return value
    return None


def _last_number(rows: list[dict[str, object]], *keys: str) -> float | None:
    for row in reversed(rows):
        value = _float(row, *keys)
        if value is not None:
            return value
    return None


def _first(row: dict[str, object], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _text(row: dict[str, object], *keys: str) -> str:
    value = _first(row, *keys)
    return str(value).strip() if value is not None else ""


def _float(row: dict[str, object], *keys: str) -> float | None:
    value = _first(row, *keys)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int(row: dict[str, object], *keys: str) -> int | None:
    value = _float(row, *keys)
    return int(value) if value is not None else None
