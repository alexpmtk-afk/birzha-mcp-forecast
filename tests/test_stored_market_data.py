from datetime import date
from types import SimpleNamespace

import pytest

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.stored_market_data import StoredMarketDataView
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


INST = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
)
GDM5 = Instrument(
    symbol="GOLD",
    secid="GDM5",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="GOLD",
)
GDU5 = Instrument(
    symbol="GOLD",
    secid="GDU5",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="GOLD",
)


class Base:
    provider = None
    direct_resolver = None
    historical_future_resolver = None

    def resolve(self, symbol, *, as_of=None):
        return INST


class NoLiveResolveBase(Base):
    def resolve(self, symbol, *, as_of=None):
        raise AssertionError("stored validation must not use live market resolution")


def _d1(instrument: Instrument, trade_date: str) -> CandleSeries:
    return CandleSeries(
        instrument,
        "D1",
        (
            Candle(
                100,
                101,
                102,
                99,
                1000,
                10,
                f"{trade_date}T00:00:00",
                f"{trade_date}T23:59:59",
                True,
            ),
        ),
    )


def test_stored_view_reads_persisted_candles():
    store = DuckDBHistoricalCandleStore(":memory:")
    store.upsert_series(_d1(INST, "2026-09-01"))
    history = HistoricalDataService(market_data=SimpleNamespace(), store=store)
    view = StoredMarketDataView(Base(), history)
    series = view.candles(
        "SBER", timeframe="D1", from_date="2026-09-01", till_date="2026-09-01"
    )
    assert series.count == 1
    assert series.candles[0].close == 101
    assert series.source == "HISTORICAL_STORE"
    store.close()


def test_rolling_future_resolves_from_versioned_stored_session_without_live_lookup():
    store = DuckDBHistoricalCandleStore(":memory:")
    store.upsert_series(_d1(GDM5, "2025-01-10"))
    session_key = _verification_symbol("GOLD", "D1", is_root=True)
    store.record_sessions(session_key, "GDM5", ("2025-01-10",))
    store.mark_session_range_verified(session_key, "2025-01-10", "2025-01-10")
    history = HistoricalDataService(
        market_data=SimpleNamespace(direct_resolver=object()),
        store=store,
    )
    view = StoredMarketDataView(NoLiveResolveBase(), history)

    instrument = view.resolve("GOLD", as_of=date(2025, 1, 10))

    assert instrument.secid == "GDM5"
    assert instrument.root_symbol == "GOLD"
    store.close()


def test_rolling_future_stored_session_fails_closed_if_contract_is_ambiguous():
    store = DuckDBHistoricalCandleStore(":memory:")
    store.upsert_series(_d1(GDM5, "2025-01-10"))
    store.upsert_series(_d1(GDU5, "2025-01-10"))
    session_key = _verification_symbol("GOLD", "D1", is_root=True)
    store.record_sessions(session_key, "GDM5", ("2025-01-10",))
    store.record_sessions(session_key, "GDU5", ("2025-01-10",))
    store.mark_session_range_verified(session_key, "2025-01-10", "2025-01-10")
    history = HistoricalDataService(
        market_data=SimpleNamespace(direct_resolver=object()),
        store=store,
    )
    view = StoredMarketDataView(NoLiveResolveBase(), history)

    with pytest.raises(RuntimeError, match="exactly one contract"):
        view.resolve("GOLD", as_of=date(2025, 1, 10))
    store.close()


def test_rolling_future_stored_session_requires_current_verified_calendar():
    store = DuckDBHistoricalCandleStore(":memory:")
    store.upsert_series(_d1(GDM5, "2025-01-10"))
    history = HistoricalDataService(
        market_data=SimpleNamespace(direct_resolver=object()),
        store=store,
    )
    view = StoredMarketDataView(NoLiveResolveBase(), history)

    with pytest.raises(RuntimeError, match="stored futures session is not verified"):
        view.resolve("GOLD", as_of=date(2025, 1, 10))
    store.close()
