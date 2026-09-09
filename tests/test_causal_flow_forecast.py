from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.application.flow import MarketFlowService
from birzha.application.forecast import _flow_adjustment, build_forecast_from_snapshot
from birzha.domain.flow import ClientOpenInterest, MarketFlowSnapshot
from birzha.domain.market import Instrument
from birzha.domain.snapshot import MarketSnapshot, TimeframeState


INSTRUMENT = Instrument(
    symbol="Si",
    secid="SiU6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="Si",
)


@dataclass
class FakeMarketData:
    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        assert symbol == "Si"
        assert as_of == date(2026, 8, 28)
        return INSTRUMENT


@dataclass
class FutureLeakingAnalytics:
    def fetch_tradestats(self, instrument: Instrument, *, from_date: str, till_date: str):
        return [
            {
                "tradedate": "2026-08-28",
                "tradetime": "12:55:00",
                "pr_open": 100.0,
                "pr_close": 101.0,
                "vol_b": 0,
                "vol_s": 20,
                "val_b": 0,
                "val_s": 2000,
                "oi_open": 1000,
                "oi_close": 1010,
            },
            {
                "tradedate": "2026-08-28",
                "tradetime": "13:05:00",
                "pr_open": 101.0,
                "pr_close": 110.0,
                "vol_b": 1000,
                "vol_s": 0,
                "val_b": 110000,
                "val_s": 0,
                "oi_open": 1010,
                "oi_close": 1500,
            },
        ]

    def fetch_futoi(self, instrument: Instrument, *, from_date: str, till_date: str):
        return [
            {
                "tradedate": "2026-08-28",
                "tradetime": "12:55:00",
                "clgroup": "FIZ",
                "pos": 0,
                "pos_long": 500,
                "pos_short": -500,
                "pos_long_num": 10,
                "pos_short_num": 10,
            },
            {
                "tradedate": "2026-08-28",
                "tradetime": "13:05:00",
                "clgroup": "FIZ",
                "pos": 900,
                "pos_long": 1400,
                "pos_short": -500,
                "pos_long_num": 20,
                "pos_short_num": 10,
            },
        ]


def test_flow_cutoff_removes_future_rows_and_preserves_real_zero() -> None:
    service = MarketFlowService(
        market_data=FakeMarketData(),  # type: ignore[arg-type]
        analytics=FutureLeakingAnalytics(),  # type: ignore[arg-type]
    )

    flow = service.build(
        "Si",
        from_date="2026-08-28",
        till_date="2026-08-28",
        cutoff_at="2026-08-28T13:00:00+03:00",
    )

    assert flow.intervals == 1
    assert flow.buy_volume == 0.0
    assert flow.sell_volume == 20.0
    assert flow.volume_delta == -20.0
    assert flow.volume_delta_ratio == -1.0
    assert flow.individuals is not None
    assert flow.individuals.net_position == 0.0
    assert flow.as_of is not None
    assert "12:55:00" in flow.as_of


def _state(tf: str, trend: float) -> TimeframeState:
    return TimeframeState(
        timeframe=tf,
        candles=100,
        last_close=100.0,
        return_5=0.01,
        return_10=0.02,
        return_20=0.03,
        sma_20=98.0,
        sma_50=95.0,
        efficiency_ratio_20=0.5,
        atr_14_pct=0.01 if tf == "D1" else None,
        volume_ratio_20=1.1,
        trend_score=trend,
    )


def _flow(delta_ratio: float, price_change: float, oi_change: float) -> MarketFlowSnapshot:
    return MarketFlowSnapshot(
        symbol="Si",
        secid="SiU6",
        from_date="2026-08-24",
        till_date="2026-08-28",
        as_of="2026-08-28T12:55:00+03:00",
        source="MOEX_ALGOPACK+FUTOI",
        intervals=10,
        buy_volume=100.0,
        sell_volume=100.0,
        volume_delta=0.0,
        volume_delta_ratio=delta_ratio,
        buy_value=1.0,
        sell_value=1.0,
        value_delta=0.0,
        price_change_pct=price_change,
        algopack_oi_open=1000.0,
        algopack_oi_close=1000.0 + oi_change,
        algopack_oi_change=oi_change,
        individuals=ClientOpenInterest("FIZ", 100.0, 600.0, -500.0, 10, 9, "2026-08-28T12:55:00+03:00"),
        legal_entities=ClientOpenInterest("YUR", -100.0, 200.0, -300.0, 4, 5, "2026-08-28T12:55:00+03:00"),
        data_quality="PASS",
        warnings=(),
    )


def test_flow_refines_but_cannot_dominate_long_horizon_score() -> None:
    snapshot = MarketSnapshot(
        symbol="Si",
        secid="SiU6",
        as_of="2026-08-28T13:00:00+03:00",
        source="MOEX_ISS+ALGOPACK+FUTOI",
        d1=_state("D1", 3.0),
        h1=_state("H1", 2.0),
        m15=_state("M15", 1.0),
        data_quality="PASS",
        warnings=(),
        flow=_flow(-1.0, -2.0, 300.0),
    )

    assert _flow_adjustment(snapshot) == -0.75
    forecast = build_forecast_from_snapshot(snapshot)
    assert forecast.direction == "UP"
    assert forecast.engine_version == "BIRZHA_FORECAST_BASELINE_V0_3_PROFILE"
    assert any(reason.startswith("FLOW:") for reason in forecast.reasons)
    assert any(reason.startswith("FUTOI:") for reason in forecast.reasons)
