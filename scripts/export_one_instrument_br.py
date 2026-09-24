from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from birzha.application.market_data import MarketDataService
from birzha.providers.moex_analytics import MoexAnalyticsClient


SYMBOL = "BR"
TIMEFRAME_WINDOWS = {"H1": 90, "M15": 30}
WARMUP_DAYS = {"H1": 90, "M15": 30}

RAW_COLUMNS = [
    "record_key",
    "calendar_date",
    "trade_session_date",
    "session_id",
    "bar_start_time",
    "bar_end_time",
    "available_at",
    "available_at_confidence",
    "contract_code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "number_of_trades",
    "turnover",
    "is_complete",
    "source_interval",
    "source_candles",
    "source_id",
]

INDICATOR_COLUMNS = [
    "record_key",
    "calendar_date",
    "trade_session_date",
    "bar_start_time",
    "bar_end_time",
    "contract_code",
    "timeframe",
    "close",
    "volume",
    "return_5",
    "return_10",
    "return_20",
    "sma_20",
    "sma_50",
    "efficiency_ratio_20",
    "atr_14_pct",
    "volume_ratio_20",
    "vwap_20",
    "price_location_20",
    "support_20",
    "resistance_20",
    "trend_score",
    "source_id",
    "calculated_at",
]


@dataclass(frozen=True)
class CandleRow:
    secid: str
    timeframe: str
    begin: str
    end: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    value: float | None


def _iso_day(value: str) -> str:
    return value[:10]


def _segments(service: MarketDataService, start: date, finish: date):
    resolver = service.historical_future_resolver
    assert resolver is not None
    timeline = list(resolver.timeline(SYMBOL, start, finish))
    if not timeline:
        instrument = service.resolve(SYMBOL, as_of=finish)
        return {instrument.secid: (instrument, {start})}

    grouped: dict[str, tuple[Any, set[date]]] = {}
    for day, instrument in timeline:
        if instrument.secid not in grouped:
            grouped[instrument.secid] = (instrument, set())
        grouped[instrument.secid][1].add(day)

    # The historical selector may lag on the current exchange day. Ensure the
    # requested end date can still resolve through the live active-contract list.
    if finish not in {d for _, days in grouped.values() for d in days}:
        try:
            current = service.provider.resolve_active_future(SYMBOL, as_of=finish)
        except Exception:
            current = None
        if current is not None:
            if current.secid not in grouped:
                grouped[current.secid] = (current, set())
            grouped[current.secid][1].add(finish)
    return grouped


def _series_rows(service: MarketDataService, timeframe: str, start: date, finish: date):
    grouped = _segments(service, start, finish)
    all_raw: list[list[Any]] = []
    all_indicators: list[list[Any]] = []
    per_contract: dict[str, int] = {}
    for secid, (instrument, active_days) in sorted(grouped.items()):
        first = min(active_days)
        last = max(active_days)
        context_start = first - timedelta(days=WARMUP_DAYS[timeframe])
        series = service.candles_for_instrument(
            instrument,
            timeframe=timeframe,
            from_date=context_start.isoformat(),
            till_date=last.isoformat(),
            completed_only=True,
        )
        candles = list(series.candles)
        if not candles:
            continue
        output_indexes = [
            i
            for i, candle in enumerate(candles)
            if date.fromisoformat(_iso_day(candle.begin)) in active_days
            and start <= date.fromisoformat(_iso_day(candle.begin)) <= finish
        ]
        per_contract[secid] = len(output_indexes)
        for i in output_indexes:
            candle = candles[i]
            key = f"{SYMBOL}|{timeframe}|{secid}|{candle.begin}"
            day = _iso_day(candle.begin)
            all_raw.append(
                [
                    key,
                    day,
                    day,
                    "",
                    candle.begin,
                    candle.end,
                    candle.end,
                    "INFERRED",
                    secid,
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                    "",
                    candle.value,
                    bool(candle.completed),
                    timeframe,
                    15 if timeframe == "M15" else 1,
                    series.source,
                ]
            )
            all_indicators.append(
                _indicator_row(
                    candles,
                    i,
                    key=key,
                    secid=secid,
                    timeframe=timeframe,
                    calculated_at=finish.isoformat(),
                )
            )
    all_raw.sort(key=lambda row: (str(row[4]), str(row[8])))
    all_indicators.sort(key=lambda row: (str(row[3]), str(row[5])))
    return all_raw, all_indicators, per_contract


def _indicator_row(candles, i: int, *, key: str, secid: str, timeframe: str, calculated_at: str):
    closes = [c.close for c in candles[: i + 1]]
    volumes = [c.volume for c in candles[: i + 1]]

    def ret(n: int):
        if len(closes) <= n or closes[-1] is None or closes[-n - 1] in (None, 0):
            return None
        return closes[-1] / closes[-n - 1] - 1.0

    def sma(n: int):
        if len(closes) < n:
            return None
        window = closes[-n:]
        if any(v is None for v in window):
            return None
        return sum(window) / n

    def er(n: int):
        if len(closes) <= n:
            return None
        window = closes[-(n + 1) :]
        if any(v is None for v in window):
            return None
        direction = abs(window[-1] - window[0])
        noise = sum(abs(b - a) for a, b in zip(window, window[1:]))
        return direction / noise if noise else 0.0

    def atr_pct(n: int):
        window = candles[max(0, i - n) : i + 1]
        if len(window) < n + 1:
            return None
        if any(c.high is None or c.low is None or c.close is None for c in window):
            return None
        trs = []
        for prev, cur in zip(window[:-1], window[1:]):
            trs.append(
                max(
                    cur.high - cur.low,
                    abs(cur.high - prev.close),
                    abs(cur.low - prev.close),
                )
            )
        return (sum(trs) / len(trs)) / window[-1].close if window[-1].close else None

    def volume_ratio(n: int):
        if len(volumes) < n + 1 or volumes[-1] is None:
            return None
        base_window = volumes[-(n + 1) : -1]
        if any(v is None for v in base_window):
            return None
        baseline = sum(base_window) / n
        return volumes[-1] / baseline if baseline else None

    window20 = candles[max(0, i - 19) : i + 1]
    weighted = 0.0
    volume_sum = 0.0
    for c in window20:
        price = (
            (c.high + c.low + c.close) / 3.0
            if c.high is not None and c.low is not None and c.close is not None
            else c.close
        )
        if price is None or c.volume is None or c.volume <= 0:
            continue
        weighted += price * c.volume
        volume_sum += c.volume
    vwap20 = weighted / volume_sum if volume_sum else None

    highs = [c.high for c in window20 if c.high is not None]
    lows = [c.low for c in window20 if c.low is not None]
    closes20 = [c.close for c in window20 if c.close is not None]
    support20 = min(lows) if lows else None
    resistance20 = max(highs) if highs else None
    if not highs or not lows or not closes20:
        location20 = None
    elif resistance20 == support20:
        location20 = 0.5
    else:
        location20 = (closes20[-1] - support20) / (resistance20 - support20)

    sma20 = sma(20)
    sma50 = sma(50)
    r20 = ret(20)
    e20 = er(20)
    last_close = closes[-1] if closes else None
    trend = 0.0
    if last_close is not None:
        if sma20 is not None:
            trend += 1.0 if last_close > sma20 else -1.0
        if sma20 is not None and sma50 is not None:
            trend += 1.0 if sma20 > sma50 else -1.0
        if r20 is not None:
            trend += 1.0 if r20 > 0 else -1.0
        if e20 is not None:
            trend *= 0.5 + min(1.0, e20)

    candle = candles[i]

    def r6(value):
        return None if value is None else round(float(value), 6)

    return [
        key + "|IND",
        _iso_day(candle.begin),
        _iso_day(candle.begin),
        candle.begin,
        candle.end,
        secid,
        timeframe,
        candle.close,
        candle.volume,
        r6(ret(5)),
        r6(ret(10)),
        r6(r20),
        r6(sma20),
        r6(sma50),
        r6(e20),
        r6(atr_pct(14)),
        r6(volume_ratio(20)),
        r6(vwap20),
        r6(location20),
        r6(support20),
        r6(resistance20),
        r6(trend),
        f"DERIVED_FROM_{timeframe}",
        calculated_at,
    ]


def _flow_rows(service: MarketDataService, start: date, finish: date):
    analytics = MoexAnalyticsClient(
        bearer_token=os.getenv("MOEX_ALGOPACK_BEARER_TOKEN") or None
    )
    grouped = _segments(service, start, finish)

    trade_rows: list[dict[str, Any]] = []
    trade_errors: list[str] = []
    for secid, (instrument, active_days) in sorted(grouped.items()):
        left = min(active_days).isoformat()
        right = max(active_days).isoformat()
        try:
            rows = analytics.fetch_tradestats(
                instrument, from_date=left, till_date=right
            )
            for row in rows:
                item = dict(row)
                item["contract_code"] = secid
                trade_rows.append(item)
        except Exception as exc:
            trade_errors.append(f"{secid}:{type(exc).__name__}:{str(exc)[:300]}")

    try:
        current = service.resolve(SYMBOL, as_of=finish)
        futoi_rows = analytics.fetch_futoi(
            current, from_date=start.isoformat(), till_date=finish.isoformat()
        )
        futoi_error = None
    except Exception as exc:
        futoi_rows = []
        futoi_error = f"{type(exc).__name__}:{str(exc)[:500]}"

    return trade_rows, futoi_rows, trade_errors, futoi_error


def _table_from_dicts(rows: list[dict[str, Any]], extra_first: list[str] | None = None):
    if not rows:
        return [], []
    keys: list[str] = []
    for key in extra_first or []:
        if any(key in row for row in rows) and key not in keys:
            keys.append(key)
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    return keys, [[row.get(key) for key in keys] for row in rows]


def _write_json(path: Path, payload: dict[str, Any]):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    finish = date.fromisoformat(args.as_of)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    service = MarketDataService.default()
    summary: dict[str, Any] = {
        "symbol": SYMBOL,
        "as_of": finish.isoformat(),
        "timeframes": {},
        "flow": {},
    }

    for timeframe, days in TIMEFRAME_WINDOWS.items():
        start = finish - timedelta(days=days)
        raw, indicators, contracts = _series_rows(service, timeframe, start, finish)
        _write_json(
            output / f"{timeframe}.json",
            {"headers": RAW_COLUMNS, "rows": raw},
        )
        _write_json(
            output / f"{timeframe}_INDICATORS.json",
            {"headers": INDICATOR_COLUMNS, "rows": indicators},
        )
        summary["timeframes"][timeframe] = {
            "from_date": start.isoformat(),
            "till_date": finish.isoformat(),
            "raw_rows": len(raw),
            "indicator_rows": len(indicators),
            "contracts": contracts,
        }

    flow_start = finish - timedelta(days=30)
    trade_rows, futoi_rows, trade_errors, futoi_error = _flow_rows(
        service, flow_start, finish
    )
    trade_headers, trade_matrix = _table_from_dicts(
        trade_rows,
        ["contract_code", "tradedate", "tradetime", "secid"],
    )
    futoi_headers, futoi_matrix = _table_from_dicts(
        futoi_rows,
        ["tradedate", "tradetime", "ticker", "clgroup"],
    )
    _write_json(
        output / "TRADESTATS.json",
        {
            "headers": trade_headers,
            "rows": trade_matrix,
            "errors": trade_errors,
        },
    )
    _write_json(
        output / "FUTOI.json",
        {
            "headers": futoi_headers,
            "rows": futoi_matrix,
            "error": futoi_error,
        },
    )
    summary["flow"] = {
        "from_date": flow_start.isoformat(),
        "till_date": finish.isoformat(),
        "tradestats_rows": len(trade_matrix),
        "tradestats_errors": trade_errors,
        "futoi_rows": len(futoi_matrix),
        "futoi_error": futoi_error,
        "algopack_authenticated": bool(os.getenv("MOEX_ALGOPACK_BEARER_TOKEN")),
    }

    _write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
