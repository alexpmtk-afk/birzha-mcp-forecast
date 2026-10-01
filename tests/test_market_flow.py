from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.application.flow import MarketFlowService
from birzha.application.public_tradestats import PUBLIC_TRADESTATS_SOURCE
from birzha.domain.market import Instrument
from birzha.upstream.safety import UpstreamRateLimited


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


@dataclass
class FakeHistoricalWithFutoiOutage:
    def tradestats(self, instrument: Instrument, *, from_date: str, till_date: str):
        assert instrument is INSTRUMENT
        return [
            {
                "tradedate": "2026-08-28",
                "tradetime": "10:05:00",
                "pr_open": 80000,
                "pr_close": 80100,
                "vol_b": 100,
                "vol_s": 60,
                "oi_open": 1_000_000,
                "oi_close": 1_020_000,
                "_source": PUBLIC_TRADESTATS_SOURCE,
            },
            {
                "tradedate": "2026-08-28",
                "tradetime": "10:10:00",
                "pr_open": 80100,
                "pr_close": 80400,
                "vol_b": 80,
                "vol_s": 40,
                "oi_open": 1_020_000,
                "oi_close": 1_050_000,
                "_source": PUBLIC_TRADESTATS_SOURCE,
            },
        ]

    def futoi(self, instrument: Instrument, *, from_date: str, till_date: str):
        raise UpstreamRateLimited(
            "upstream remained rate-limited/unavailable after retries; last_status=504"
        )


def test_market_flow_keeps_persisted_trade_analysis_when_optional_futoi_is_unavailable() -> None:
    service = MarketFlowService(
        market_data=FakeMarketData(),
        analytics=FakeAnalytics(),
        historical=FakeHistoricalWithFutoiOutage(),
    )  # type: ignore[arg-type]

    flow = service.build("Si", from_date="2026-08-28", till_date="2026-08-28")

    assert flow.intervals == 2
    assert flow.buy_volume == 180.0
    assert flow.sell_volume == 100.0
    assert flow.volume_delta == 80.0
    assert flow.price_change_pct == 0.5
    assert flow.source == "MOEX_ISS_PUBLIC_TRADES_DERIVED+FUTOI"
    assert flow.data_quality == "DEGRADED"
    assert any(item.startswith("FUTOI_UNAVAILABLE:UpstreamRateLimited:") for item in flow.warnings)
    assert "FUTOI_EMPTY" in flow.warnings


EQUITY_INSTRUMENT = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
    root_symbol="SBER",
)


@dataclass
class FakeEquityMarketData:
    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        assert symbol == "SBER"
        assert as_of == date(2026, 10, 1)
        return EQUITY_INSTRUMENT


@dataclass
class FakeEquityHistorical:
    def tradestats(self, instrument: Instrument, *, from_date: str, till_date: str):
        assert instrument is EQUITY_INSTRUMENT
        return [
            {
                "tradedate": "2026-10-01",
                "tradetime": "10:05:00",
                "pr_open": 315.0,
                "pr_close": 315.5,
                "vol_b": 100,
                "vol_s": 60,
                "val_b": 31_500.0,
                "val_s": 18_900.0,
                "_source": PUBLIC_TRADESTATS_SOURCE,
            },
            {
                "tradedate": "2026-10-01",
                "tradetime": "10:10:00",
                "pr_open": 315.5,
                "pr_close": 316.0,
                "vol_b": 80,
                "vol_s": 40,
                "val_b": 25_240.0,
                "val_s": 12_640.0,
                "_source": PUBLIC_TRADESTATS_SOURCE,
            },
        ]

    def futoi(self, instrument: Instrument, *, from_date: str, till_date: str):
        raise AssertionError("FUTOI must not be requested for equities")


def test_equity_market_flow_uses_public_trades_without_futoi() -> None:
    service = MarketFlowService(
        market_data=FakeEquityMarketData(),
        analytics=FakeAnalytics(),
        historical=FakeEquityHistorical(),
    )  # type: ignore[arg-type]

    flow = service.build("SBER", from_date="2026-10-01", till_date="2026-10-01")

    assert flow.intervals == 2
    assert flow.buy_volume == 180.0
    assert flow.sell_volume == 100.0
    assert flow.volume_delta == 80.0
    assert flow.value_delta == 25_200.0
    assert flow.price_change_pct == round((316.0 / 315.0 - 1.0) * 100.0, 6)
    assert flow.source == "MOEX_ISS_PUBLIC_TRADES_DERIVED"
    assert flow.individuals is None
    assert flow.legal_entities is None
    assert flow.data_quality == "PASS"
    assert flow.warnings == ()


INDEX_INSTRUMENT = Instrument(
    symbol="IMOEX",
    secid="IMOEX",
    board="SNDX",
    engine="stock",
    market="index",
    asset_class="index",
    root_symbol="IMOEX",
)


@dataclass
class FakeIndexMarketData:
    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        assert symbol == "IMOEX"
        assert as_of == date(2026, 9, 29)
        return INDEX_INSTRUMENT


@dataclass
class FailIfIndexAnalyticsUsed:
    def fetch_tradestats(self, *args, **kwargs):
        raise AssertionError("TradeStats must not be requested for an index")

    def fetch_futoi(self, *args, **kwargs):
        raise AssertionError("FUTOI must not be requested for an index")


def test_index_market_flow_is_explicitly_not_applicable() -> None:
    service = MarketFlowService(
        market_data=FakeIndexMarketData(),
        analytics=FailIfIndexAnalyticsUsed(),
    )  # type: ignore[arg-type]

    flow = service.build(
        "IMOEX",
        from_date="2026-09-29",
        till_date="2026-09-29",
    )

    assert flow.source == "NOT_APPLICABLE_FOR_INDEX"
    assert flow.intervals == 0
    assert flow.buy_volume is None
    assert flow.sell_volume is None
    assert flow.individuals is None
    assert flow.legal_entities is None
    assert flow.data_quality == "PASS"
    assert flow.warnings == ()
