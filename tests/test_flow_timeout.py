from birzha.application.flow import MarketFlowService
from birzha.domain.market import Instrument


class Analytics:
    def fetch_tradestats(self, *args, **kwargs):
        raise TimeoutError("temporary timeout")

    def fetch_futoi(self, *args, **kwargs):
        raise TimeoutError("temporary timeout")


def test_optional_analytics_timeout_degrades_instead_of_blocking_forecast() -> None:
    service = MarketFlowService(market_data=object(), analytics=Analytics())  # type: ignore[arg-type]
    instrument = Instrument(
        symbol="Si", secid="SiZ6", board="RFUD", engine="futures",
        market="forts", asset_class="future", root_symbol="Si",
    )

    flow = service.build_for_instrument(
        instrument,
        from_date="2026-09-23",
        till_date="2026-09-24",
        cutoff_at="2026-09-24T18:00:00+03:00",
    )

    assert flow.data_quality == "DEGRADED"
    assert flow.volume_delta is None
    assert flow.algopack_oi_change is None
    assert sum("TimeoutError" in warning for warning in flow.warnings) == 2
