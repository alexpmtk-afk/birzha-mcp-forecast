from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from birzha.application.market_data import MarketDataService, _normalize_completion
from birzha.domain.market import Candle, CandleSeries, Instrument


MOSCOW = ZoneInfo("Europe/Moscow")
INSTRUMENT = Instrument(
    symbol="Si",
    secid="SiU6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="Si",
)


def _candle(end: str, *, completed: bool = True) -> Candle:
    return Candle(
        open=100.0,
        close=101.0,
        high=102.0,
        low=99.0,
        value=1000.0,
        volume=10.0,
        begin="2026-08-30 13:00:00",
        end=end,
        completed=completed,
    )


def test_future_native_candle_is_marked_forming() -> None:
    now = datetime(2026, 8, 30, 13, 30, tzinfo=MOSCOW)
    normalized = _normalize_completion(_candle("2026-08-30 13:59:59"), now=now)
    assert normalized.completed is False


def test_closed_native_candle_remains_completed() -> None:
    now = datetime(2026, 8, 30, 14, 0, tzinfo=MOSCOW)
    normalized = _normalize_completion(_candle("2026-08-30 13:59:59"), now=now)
    assert normalized.completed is True


def test_unparseable_exchange_end_time_fails_closed() -> None:
    now = datetime(2026, 8, 30, 14, 0, tzinfo=MOSCOW)
    normalized = _normalize_completion(_candle("not-a-time"), now=now)
    assert normalized.completed is False


@dataclass
class FakeProvider:
    def fetch_candles(self, instrument: Instrument, **_: object) -> CandleSeries:
        return CandleSeries(
            instrument=instrument,
            timeframe="H1",
            candles=(
                _candle("2026-08-30 12:59:59"),
                _candle("2026-08-30 13:59:59"),
            ),
        )


def test_completed_only_excludes_current_forming_bar() -> None:
    service = MarketDataService(provider=FakeProvider())  # type: ignore[arg-type]
    now = datetime(2026, 8, 30, 13, 30, tzinfo=MOSCOW)

    result = service.candles_for_instrument(
        INSTRUMENT,
        timeframe="H1",
        from_date="2026-08-30",
        till_date="2026-08-30",
        completed_only=True,
        now=now,
    )

    assert len(result.candles) == 1
    assert result.candles[0].end == "2026-08-30 12:59:59"
