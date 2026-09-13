from __future__ import annotations

from datetime import date, datetime, timedelta

from birzha.application.snapshot import MarketSnapshotService
from birzha.domain.market import Candle, CandleSeries, Instrument


INSTRUMENT = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
)


def _candle(day: date, *, hour: int, minute: int, close: float, minutes: int) -> Candle:
    begin = datetime(day.year, day.month, day.day, hour, minute)
    end = begin + timedelta(minutes=minutes) - timedelta(seconds=1)
    return Candle(
        open=close - 0.5,
        close=close,
        high=close + 1.0,
        low=close - 1.0,
        value=1000.0,
        volume=10.0,
        begin=begin.isoformat(sep=" "),
        end=end.isoformat(sep=" "),
        completed=True,
    )


class RecordingMarket:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        assert symbol == "SBER"
        return INSTRUMENT

    def candles_for_instrument(
        self,
        instrument: Instrument,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
        completed_only: bool = True,
        **_: object,
    ) -> CandleSeries:
        self.calls.append((timeframe, from_date, till_date))
        till = date.fromisoformat(till_date)
        if timeframe == "D1":
            # One clear local low 15 days before T0, then a rising leg.
            days = [till - timedelta(days=69 - index) for index in range(70)]
            pivot = len(days) - 16
            closes = []
            for index, _day in enumerate(days):
                if index <= pivot:
                    closes.append(200.0 - index)
                else:
                    closes.append(200.0 - pivot + (index - pivot) * 2.0)
            candles = tuple(
                _candle(day, hour=18, minute=0, close=close, minutes=60)
                for day, close in zip(days, closes, strict=True)
            )
        elif timeframe == "H1":
            start = date.fromisoformat(from_date)
            candles_list = []
            cursor = start
            counter = 0
            while cursor <= till:
                for hour in range(10, 19):
                    close = 100.0 + counter * 0.1
                    candles_list.append(
                        _candle(cursor, hour=hour, minute=0, close=close, minutes=60)
                    )
                    counter += 1
                cursor += timedelta(days=1)
            candles = tuple(candles_list)
        elif timeframe == "M15":
            start = date.fromisoformat(from_date)
            candles_list = []
            cursor = start
            counter = 0
            while cursor <= till:
                for hour in range(10, 19):
                    for minute in (0, 15, 30, 45):
                        candles_list.append(
                            _candle(
                                cursor,
                                hour=hour,
                                minute=minute,
                                close=120.0 + counter * 0.01,
                                minutes=15,
                            )
                        )
                        counter += 1
                cursor += timedelta(days=1)
            candles = tuple(candles_list)
        else:
            raise AssertionError(f"unexpected timeframe {timeframe}")
        return CandleSeries(instrument, timeframe, candles, source="TEST")


def test_snapshot_is_strictly_staged_d1_then_h1_then_m15() -> None:
    market = RecordingMarket()
    service = MarketSnapshotService(market_data=market, flow=None)  # type: ignore[arg-type]

    snapshot = service.build("SBER", as_of_date="2026-09-13")

    assert [item[0] for item in market.calls] == ["D1", "H1", "M15"]
    d1_call, h1_call, m15_call = market.calls
    assert d1_call[1] == "2025-11-17"
    # Intraday does not start until the D1 leg anchor has been selected.
    assert h1_call[1] > d1_call[1]
    # M1-backed M15 is deliberately bounded to a short tactical window.
    assert date.fromisoformat(m15_call[1]) >= date(2026, 9, 3)
    assert m15_call[2] == "2026-09-13"
    assert snapshot.d1.candles >= 50
    assert snapshot.h1.candles >= 50
    assert snapshot.m15.candles >= 50
