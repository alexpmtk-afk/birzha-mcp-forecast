"""Bounded research-only MOEX H1/M15 one-day adapter.

MOEX has native M1 and H1, not native M15. A research M15 is admitted only
when all fifteen distinct completed native minute candles are present.
Missing minutes may mean no trades OR absent data; we cannot certify a full
quarter from incomplete price evidence, so it is excluded, not interpolated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from birzha.application.history_policy import require_historical_intraday_scope
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import _check, _moment
from birzha.domain.market import Candle, CandleSeries, Instrument

_MOSCOW = ZoneInfo("Europe/Moscow")


@dataclass(frozen=True)
class ResearchDay:
    instrument: Instrument
    trade_date: str
    hourly: CandleSeries
    quarter_hour: CandleSeries
    minute_rows_seen: int
    incomplete_m15_buckets: int
    skipped_h1_bars: int
    m15_gap_events: tuple[dict[str, object], ...] = ()
    h1_gap_events: tuple[dict[str, object], ...] = ()


def aggregate_strict_m15(series: CandleSeries, trade_date: str, *,
                         gap_events: list[dict[str, object]] | None = None) -> tuple[CandleSeries, int]:
    if series.timeframe != "M1":
        raise ValueError("M1 exact provider input required to build M15")
    require_historical_intraday_scope("M15", trade_date, trade_date)
    bins: dict[datetime, list[tuple[datetime, Candle]]] = {}
    invalid = 0
    for candle in series.candles:
        t = _moment(candle.begin)
        if t.date().isoformat() != trade_date:
            continue
        end = _moment(candle.end)
        if candle.completed is not True or (end - t).total_seconds() not in (59, 60):
            invalid += 1
            if gap_events is not None:
                gap_events.append({"kind": "INVALID_M1", "begin": t.isoformat(),
                                   "reason": "uncompleted_or_nonminute_length"})
            continue
        if t.second or t.microsecond:
            invalid += 1
            if gap_events is not None:
                gap_events.append({"kind": "INVALID_M1", "begin": t.isoformat(),
                                   "reason": "nonaligned_minute_start"})
            continue
        bucket = t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)
        bins.setdefault(bucket, []).append((t, candle))
    result: list[Candle] = []
    incomplete = invalid
    for start, values in sorted(bins.items()):
        values.sort(key=lambda item: item[0])
        expected = [start + timedelta(minutes=i) for i in range(15)]
        if [v[0] for v in values] != expected:
            incomplete += 1
            if gap_events is not None:
                seen = [v[0] for v in values]
                gap_events.append({"kind": "INCOMPLETE_M15", "bucket_start": start.isoformat(),
                                   "minute_count": len(seen),
                                   "missing_m1": [t.isoformat() for t in expected if t not in seen],
                                   "duplicate_m1": len(seen) - len(set(seen))})
            continue
        group = [v[1] for v in values]
        if any(x is None for c in group for x in (c.open, c.close, c.high, c.low)):
            incomplete += 1
            if gap_events is not None:
                gap_events.append({"kind": "INCOMPLETE_M15", "bucket_start": start.isoformat(),
                                   "reason": "missing_OHLC"})
            continue
        end = start + timedelta(minutes=15) - timedelta(seconds=1)
        bar = Candle(
            open=group[0].open, close=group[-1].close,
            high=max(c.high for c in group), low=min(c.low for c in group),
            value=(sum(c.value for c in group)
                   if all(c.value is not None for c in group) else None),
            volume=(sum(c.volume for c in group)
                    if all(c.volume is not None for c in group) else None),
            begin=start.isoformat(), end=end.isoformat(),
            completed=True, available_at=end.isoformat(),
            available_at_confidence="INFERRED", source="MOEX_M1_AGGREGATED_M15",
        )
        try:
            _check(bar, "M15")
        except ValueError as exc:
            incomplete += 1
            if gap_events is not None:
                gap_events.append({"kind": "INVALID_M15", "bucket_start": start.isoformat(),
                                   "reason": str(exc)[:200]})
            continue
        result.append(bar)
    return CandleSeries(series.instrument, "M15", tuple(result),
                        source="MOEX_M1_AGGREGATED_M15"), incomplete


def prepare_one_day(market_data: MarketDataService, instrument: Instrument,
                    trade_date: str, *, current_date: str | None = None) -> ResearchDay:
    require_historical_intraday_scope("H1", trade_date, trade_date)
    if current_date is None:
        current_date = datetime.now(_MOSCOW).date().isoformat()
    if trade_date >= current_date:
        raise ValueError("only completed past Moscow trading days are research eligible")
    # Two native ISS source requests, each handled by the existing MOEX governor.
    h1 = market_data.candles_for_instrument(
        instrument, timeframe="H1", from_date=trade_date,
        till_date=trade_date, completed_only=True)
    m1 = market_data.candles_for_instrument(
        instrument, timeframe="M1", from_date=trade_date,
        till_date=trade_date, completed_only=True)
    hourly, skipped = [], 0
    h1_events: list[dict[str, object]] = []
    m15_events: list[dict[str, object]] = []
    for bar in h1.candles:
        if _moment(bar.begin).date().isoformat() != trade_date:
            continue
        try:
            _check(bar, "H1")
        except ValueError as exc:
            skipped += 1
            h1_events.append({"kind": "INVALID_H1", "begin": bar.begin,
                              "end": bar.end, "reason": str(exc)[:200]})
        else:
            hourly.append(bar)
    m15, incomplete = aggregate_strict_m15(m1, trade_date, gap_events=m15_events)
    return ResearchDay(
        instrument=instrument, trade_date=trade_date,
        hourly=CandleSeries(instrument, "H1", tuple(hourly), source="MOEX_NATIVE_H1"),
        quarter_hour=m15, minute_rows_seen=len(m1.candles),
        incomplete_m15_buckets=incomplete, skipped_h1_bars=skipped,
        m15_gap_events=tuple(m15_events), h1_gap_events=tuple(h1_events),
    )
