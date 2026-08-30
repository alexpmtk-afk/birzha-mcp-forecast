from json import JSONDecodeError

from birzha.application.flow import MarketFlowService
from birzha.domain.market import Instrument


class Analytics:
    def fetch_tradestats(self, *args, **kwargs):
        raise JSONDecodeError("bad analytics payload", "", 0)

    def fetch_futoi(self, *args, **kwargs):
        raise JSONDecodeError("bad analytics payload", "", 0)


class MarketData:
    pass


def test_malformed_optional_analytics_degrades_instead_of_killing_snapshot():
    service = MarketFlowService(market_data=MarketData(), analytics=Analytics())
    instrument = Instrument(
        symbol="Si",
        secid="SiM6",
        board="RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        root_symbol="Si",
    )

    flow = service.build_for_instrument(
        instrument,
        from_date="2026-04-01",
        till_date="2026-04-02",
        cutoff_at="2026-04-02T18:00:00+03:00",
    )

    assert flow.data_quality == "DEGRADED"
    assert flow.volume_delta is None
    assert flow.algopack_oi_change is None
    assert any("JSONDecodeError" in warning for warning in flow.warnings)
