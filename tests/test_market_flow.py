from __future__ import annotations

from dataclasses import dataclass

from birzha.application.flow import MarketFlowService
from birzha.domain.market import Instrument


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
    def resolve(self, symbol: str) -> Instrument:
        assert symbol == "Si"
        return INSTRUMENT


@dataclass
class FakeAnalytics:
    def fetch_tradestats(self, instrument: Instrument, *, from_date: str, till_date: str):
        assert instrument is INSTRUMENT
        return [
            {
                "tradedate": "2026-08-28",
                "tradetime": "10:05:00",
                "pr_open": 80000,
                "pr_close": 80100,
                "vol_b": 100,
                "vol_s": 60,
                "val_b": 8_000_000,
                "val_s": 4_800_000,
                "oi_open": 1_000_000,
                "oi_close": 1_020_000,
            },
            {
                "tradedate": "2026-08-28",
                "tradetime": "10:10:00",
                "pr_open": 80100,
                "pr_close": 80400,
                "vol_b": 80,
                "vol_s": 40,
                "val_b": 6_400_000,
                "val_s": 3_200_000,
                "oi_open": 1_020_000,
                "oi_close": 1_050_000,
            },
        ]

    def fetch_futoi(self, instrument: Instrument, *, from_date: str, till_date: str):
        assert instrument is INSTRUMENT
        return [
            {
                "tradedate": "2026-08-28",
                "tradetime": "10:05:00",
                "clgroup": "FIZ",
                "pos": 100_000,
                "pos_long": 600_000,
                "pos_short": -500_000,
                "pos_long_num": 1000,
                "pos_short_num": 800,
            },
            {
                "tradedate": "2026-08-28",
                "tradetime": "10:10:00",
                "clgroup": "FIZ",
                "pos": 120_000,
                "pos_long": 630_000,
                "pos_short": -510_000,
                "pos_long_num": 1010,
                "pos_short_num": 805,
            },
            {
                "tradedate": "2026-08-28",
                "tradetime": "10:10:00",
                "clgroup": "YUR",
                "pos": -120_000,
                "pos_long": 210_000,
                "pos_short": -330_000,
                "pos_long_num": 90,
                "pos_short_num": 70,
            },
        ]


def test_market_flow_aggregates_real_contract_semantics() -> None:
    service = MarketFlowService(market_data=FakeMarketData(), analytics=FakeAnalytics())  # type: ignore[arg-type]

    flow = service.build("Si", from_date="2026-08-28", till_date="2026-08-28")

    assert flow.intervals == 2
    assert flow.buy_volume == 180.0
    assert flow.sell_volume == 100.0
    assert flow.volume_delta == 80.0
    assert flow.volume_delta_ratio == round(80 / 280, 6)
    assert flow.value_delta == 6_400_000.0
    assert flow.price_change_pct == 0.5
    assert flow.algopack_oi_open == 1_000_000.0
    assert flow.algopack_oi_close == 1_050_000.0
    assert flow.algopack_oi_change == 50_000.0
    assert flow.individuals is not None
    assert flow.individuals.net_position == 120_000.0
    assert flow.legal_entities is not None
    assert flow.legal_entities.net_position == -120_000.0
    assert flow.data_quality == "PASS"
    assert flow.warnings == ()
