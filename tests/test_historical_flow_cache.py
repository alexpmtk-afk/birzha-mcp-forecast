from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.domain.market import Instrument
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore


class FakeAnalytics:
    def __init__(self):
        self.trade_calls=0
        self.futoi_calls=0
    def fetch_tradestats(self,instrument,*,from_date,till_date):
        self.trade_calls += 1
        return [{"tradedate":from_date,"tradetime":"10:00:00","seqnum":1,"vol_b":10,"vol_s":5}]
    def fetch_futoi(self,instrument,*,from_date,till_date):
        self.futoi_calls += 1
        return [{"tradedate":from_date,"tradetime":"10:00:00","clgroup":"FIZ","pos":100}]


class FakeMarketData:
    direct_resolver=None
    historical_future_resolver=None


FUT=Instrument(symbol="Si",secid="SiU6",board="RFUD",engine="futures",market="forts",asset_class="future",root_symbol="Si")


def test_repeated_tradestats_range_uses_store_without_second_upstream_call():
    analytics=FakeAnalytics()
    store=DuckDBHistoricalFlowStore(":memory:")
    service=HistoricalFlowDataService(market_data=FakeMarketData(),analytics=analytics,store=store)
    first=service.tradestats(FUT,from_date="2026-09-01",till_date="2026-09-02")
    second=service.tradestats(FUT,from_date="2026-09-01",till_date="2026-09-02")
    assert first == second
    assert analytics.trade_calls == 1
    store.close()


def test_repeated_futoi_range_uses_store_without_second_upstream_call():
    analytics=FakeAnalytics()
    store=DuckDBHistoricalFlowStore(":memory:")
    service=HistoricalFlowDataService(market_data=FakeMarketData(),analytics=analytics,store=store)
    first=service.futoi(FUT,from_date="2026-09-01",till_date="2026-09-02")
    second=service.futoi(FUT,from_date="2026-09-01",till_date="2026-09-02")
    assert first == second
    assert analytics.futoi_calls == 1
    store.close()
