from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.application.snapshot import MarketSnapshotService
from birzha.domain.market import Candle, CandleSeries, Instrument


INSTRUMENT = Instrument(
    symbol="Si",
    secid="SiU6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="Si",
)


def _candle(begin: str, end: str, close: float) -> Candle:
    return Candle(
        open=close - 1,
        close=close,
        high=close + 1,
        low=close - 2,
        value=1000.0,
        volume=10.0,
        begin=begin,
        end=end,
        completed=True,
    )


@dataclass
class FakeMarketData:
    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        assert symbol == "Si"
        assert as_of == date(2026, 8, 28)
        return INSTRUMENT

    def candles_for_instrument(self, instrument: Instrument, *, timeframe: str, **_: object) -> CandleSeries:
        if timeframe == "D1":
            candles = tuple(
                _candle(f"2026-06-{day:02d} 10:00:00", f"2026-06-{day:02d} 23:49:59", 100 + day)
                for day in range(1, 31)
            ) + tuple(
                _candle(f"2026-07-{day:02d} 10:00:00", f"2026-07-{day:02d} 23:49:59", 130 + day)
                for day in range(1, 32)
            ) + (_candle("2026-08-27 10:00:00", "2026-08-27 23:49:59", 170.0),)
        elif timeframe == "H1":
            candles = tuple(
                _candle(f"2026-08-28 {hour:02d}:00:00", f"2026-08-28 {hour:02d}:59:59", 200 + hour)
                for hour in range(0, 13)
            )
        else:
            candles = tuple(
                _candle(
                    f"2026-08-28 12:{minute:02d}:00",
                    f"2026-08-28 12:{minute + 14:02d}:59",
                    300 + minute,
                )
                for minute in (0, 15, 30, 45)
            ) + (_candle("2026-08-28 13:00:00", "2026-08-28 13:14:59", 400.0),)
        return CandleSeries(instrument=instrument, timeframe=timeframe, candles=candles)


def test_snapshot_t0_is_latest_completed_observation_not_oldest_timeframe_end() -> None:
    service = MarketSnapshotService(market_data=FakeMarketData(), flow=None)  # type: ignore[arg-type]

    snapshot = service.build("Si", as_of_date="2026-08-28")

    assert snapshot.contract_version == "MARKET_SNAPSHOT_V2"
    assert snapshot.to_dict()["contract_version"] == "MARKET_SNAPSHOT_V2"
    assert snapshot.as_of == "2026-08-28 13:14:59"
    assert snapshot.d1.candles > 0
    assert snapshot.h1.candles > 0
    assert snapshot.m15.candles > 0

    quality = snapshot.quality_contract
    assert quality is not None
    assert quality.version == "DATA_QUALITY_CONTRACT_V2"
    assert quality.status == "DEGRADED"
    by_tf = {item.timeframe: item for item in quality.timeframes}
    assert by_tf["D1"].status == "PASS"
    assert by_tf["H1"].status == "DEGRADED"
    assert by_tf["M15"].status == "DEGRADED"
    assert quality.flow_status == "NOT_REQUESTED"
    assert snapshot.data_quality == quality.status
    assert any(reason.startswith("H1: insufficient_history") for reason in quality.reasons)
