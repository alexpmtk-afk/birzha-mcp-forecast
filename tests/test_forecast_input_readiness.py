from birzha.application.forecast_input_readiness import ForecastInputReadinessService
from birzha.domain.market import Instrument


class _MarketData:
    def __init__(self, instrument: Instrument) -> None:
        self.instrument = instrument

    def resolve(self, symbol: str):
        return self.instrument


def test_future_readiness_marks_flow_partial_and_session_vwap_missing() -> None:
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
    assert report["items"]["open_interest"]["status"] == "PARTIAL"
    assert report["items"]["delta"]["status"] == "PARTIAL"
    assert report["items"]["cumulative_delta"]["status"] == "PARTIAL"
    assert report["items"]["session_vwap"]["status"] == "MISSING"
    assert report["items"]["number_of_trades"]["status"] == "PARTIAL"
    assert report["items"]["volume_profile"]["status"] == "PARTIAL"


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
    assert report["items"]["session_vwap"]["status"] == "MISSING"


def test_equity_turnover_is_ready_but_trade_count_is_not_yet_implemented() -> None:
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
    assert report["items"]["number_of_trades"]["status"] == "PARTIAL"
    assert "number_of_trades" in report["blocking_items"]
