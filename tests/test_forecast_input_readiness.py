from birzha.application.forecast_input_readiness import ForecastInputReadinessService
from birzha.domain.market import Instrument


class _MarketData:
    def __init__(self, instrument: Instrument) -> None:
        self.instrument = instrument

    def resolve(self, symbol: str):
        return self.instrument


def test_future_readiness_marks_implemented_flow_ready_and_keeps_coverage_caveats() -> None:
    instrument = Instrument(
        symbol="Si",
        secid="SiZ6",
        board="RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        root_symbol="Si",
        data_capabilities=(
            "CANDLES",
            "TRADING_CALENDAR",
            "VOLUME",
            "TURNOVER",
            "TRADESTATS",
            "OPEN_INTEREST",
            "FUTOI",
        ),
        roll_policy="MOEX_CAUSAL_LIQUIDITY_AS_OF_DATE",
    )
    report = ForecastInputReadinessService(_MarketData(instrument)).audit("Si")

    assert report["ready_for_protocol_08"] is False
    assert report["items"]["open_interest"]["status"] == "READY"
    assert report["items"]["delta"]["status"] == "READY"
    assert report["items"]["cumulative_delta"]["status"] == "READY"
    assert report["items"]["session_vwap"]["status"] == "READY"
    assert report["items"]["number_of_trades"]["status"] == "READY"
    assert report["items"]["volume_profile"]["status"] == "READY"
    assert report["items"]["normalized_features"]["status"] == "READY"
    assert report["blocking_items"] == []
    assert report["ready_for_protocol_08"] is True
    assert len(report["coverage_caveats"]) == 2


def test_index_readiness_does_not_require_flow_or_open_interest() -> None:
    instrument = Instrument(
        symbol="IMOEX",
        secid="IMOEX",
        board="SNDX",
        engine="stock",
        market="index",
        asset_class="index",
        data_capabilities=("CANDLES", "TRADING_CALENDAR"),
        roll_policy="NOT_APPLICABLE",
    )
    report = ForecastInputReadinessService(_MarketData(instrument)).audit("IMOEX")

    assert report["items"]["open_interest"]["status"] == "NOT_APPLICABLE"
    assert report["items"]["delta"]["status"] == "NOT_APPLICABLE"
    assert report["items"]["cumulative_delta"]["status"] == "NOT_APPLICABLE"
    assert report["items"]["number_of_trades"]["status"] == "NOT_APPLICABLE"
    assert report["items"]["session_vwap"]["status"] == "NOT_APPLICABLE"
    assert report["items"]["volume_profile"]["status"] == "NOT_APPLICABLE"
    assert report["items"]["normalized_features"]["status"] == "READY"


def test_equity_trade_features_are_implemented_but_normalized_layer_still_blocks() -> None:
    instrument = Instrument(
        symbol="SBER",
        secid="SBER",
        board="TQBR",
        engine="stock",
        market="shares",
        asset_class="equity",
        data_capabilities=(
            "CANDLES",
            "TRADING_CALENDAR",
            "VOLUME",
            "TURNOVER",
            "TRADESTATS",
        ),
        roll_policy="NOT_APPLICABLE",
    )
    report = ForecastInputReadinessService(_MarketData(instrument)).audit("SBER")

    assert report["items"]["turnover"]["status"] == "READY"
    assert report["items"]["number_of_trades"]["status"] == "READY"
    assert report["items"]["delta"]["status"] == "READY"
    assert report["items"]["cumulative_delta"]["status"] == "READY"
    assert report["items"]["session_vwap"]["status"] == "READY"
    assert report["items"]["volume_profile"]["status"] == "READY"
    assert report["blocking_items"] == []
    assert report["ready_for_protocol_08"] is True
