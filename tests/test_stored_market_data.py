from types import SimpleNamespace

from birzha.application.historical_data import HistoricalDataService
from birzha.application.stored_market_data import StoredMarketDataView
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


INST=Instrument(symbol="SBER",secid="SBER",board="TQBR",engine="stock",market="shares",asset_class="equity")


class Base:
    provider=None
    direct_resolver=None
    historical_future_resolver=None
    def resolve(self,symbol,*,as_of=None):
        return INST


def test_stored_view_reads_persisted_candles():
    store=DuckDBHistoricalCandleStore(":memory:")
    store.upsert_series(CandleSeries(INST,"D1",(Candle(100,101,102,99,1000,10,"2026-09-01T00:00:00","2026-09-01T23:59:59",True),)))
    history=HistoricalDataService(market_data=SimpleNamespace(),store=store)
    view=StoredMarketDataView(Base(),history)
    series=view.candles("SBER",timeframe="D1",from_date="2026-09-01",till_date="2026-09-01")
    assert series.count == 1
    assert series.candles[0].close == 101
    assert series.source == "HISTORICAL_STORE"
    store.close()
