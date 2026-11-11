from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import birzha.application.snapshot as snapshot_module
from birzha.application.snapshot import MarketSnapshotService, _cut_at
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
    assert snapshot.normalized_features is not None
    assert snapshot.normalized_features.version == "NORMALIZED_FEATURES_V1"
    assert snapshot.to_dict()["normalized_features"]["version"] == "NORMALIZED_FEATURES_V1"
    assert any(reason.startswith("H1: insufficient_history") for reason in quality.reasons)


def test_snapshot_does_not_build_profile_without_volume_capability(monkeypatch) -> None:
    profile_calls: list[str] = []
    profile_builder = snapshot_module.profile_from_candles

    def tracked_profile_builder(series: CandleSeries, *, bins: int = 24):
        profile_calls.append(series.instrument.asset_class)
        return profile_builder(series, bins=bins)

    monkeypatch.setattr(snapshot_module, "profile_from_candles", tracked_profile_builder)

    for asset_class in ("index", "equity"):
        instrument = replace(
            INSTRUMENT,
            symbol="TEST",
            secid="TEST",
            asset_class=asset_class,  # type: ignore[arg-type]
            data_capabilities=("CANDLES", "TRADING_CALENDAR"),
        )

        class MarketDataWithoutVolume:
            def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
                assert symbol == "TEST"
                assert as_of == date(2026, 8, 28)
                return instrument

            def candles_for_instrument(
                self,
                resolved: Instrument,
                *,
                timeframe: str,
                **kwargs: object,
            ) -> CandleSeries:
                return FakeMarketData().candles_for_instrument(
                    resolved, timeframe=timeframe, **kwargs
                )

        snapshot = MarketSnapshotService(
            market_data=MarketDataWithoutVolume(), flow=None  # type: ignore[arg-type]
        ).build("TEST", as_of_date="2026-08-28")

        assert snapshot.volume_profile is None
        assert snapshot.normalized_features is not None
        assert snapshot.normalized_features.profile_method is None
        assert not any(warning.startswith("VOLUME_PROFILE:") for warning in snapshot.warnings)

    assert profile_calls == []



def test_cut_at_excludes_observation_available_after_t0() -> None:
    candle = Candle(
        open=100.0,
        close=101.0,
        high=102.0,
        low=99.0,
        value=1000.0,
        volume=10.0,
        begin="2026-08-28 12:00:00",
        end="2026-08-28 12:59:59",
        completed=True,
        available_at="2026-08-28T13:05:00+03:00",
        available_at_confidence="EXACT",
    )
    series = CandleSeries(INSTRUMENT, "H1", (candle,))

    result = _cut_at(series, "2026-08-28T13:00:00+03:00")

    assert result.count == 0


def test_cut_at_keeps_observation_available_by_t0() -> None:
    candle = Candle(
        open=100.0,
        close=101.0,
        high=102.0,
        low=99.0,
        value=1000.0,
        volume=10.0,
        begin="2026-08-28 12:00:00",
        end="2026-08-28 12:59:59",
        completed=True,
        available_at="2026-08-28T12:59:59+03:00",
        available_at_confidence="EXACT",
    )
    series = CandleSeries(INSTRUMENT, "H1", (candle,))

    result = _cut_at(series, "2026-08-28T13:00:00+03:00")

    assert result.count == 1


def test_legacy_d1_row_without_available_at_is_conservatively_delayed() -> None:
    candle = Candle(
        open=100.0,
        close=101.0,
        high=102.0,
        low=99.0,
        value=1000.0,
        volume=10.0,
        begin="2026-08-28 10:00:00",
        end="2026-08-28 20:37:34",
        completed=True,
    )
    series = CandleSeries(INSTRUMENT, "D1", (candle,))

    same_day = _cut_at(series, "2026-08-28T23:00:00+03:00")
    next_day = _cut_at(series, "2026-08-29T00:00:00+03:00")

    assert same_day.count == 0
    assert next_day.count == 1
