from datetime import date
from types import SimpleNamespace

import pytest

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.stored_market_data import (
    DailyHistoryInitializationRequiredError,
    StoredMarketDataView,
)
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


class LiveIntradayBase(Base):
    def __init__(self):
        self.calls = []

    def candles_for_instrument(
        self,
        instrument,
        *,
        timeframe,
        from_date,
        till_date,
        completed_only=True,
        now=None,
    ):
        self.calls.append((instrument.secid, timeframe, from_date, till_date))
        return CandleSeries(
            instrument,
            timeframe,
            (
                Candle(
                    100,
                    101,
                    102,
                    99,
                    1000,
                    10,
                    f"{from_date}T10:00:00",
                    f"{from_date}T10:59:59",
                    True,
                ),
            ),
            source="LIVE_TEST",
        )


class SpyHistory:
    def __init__(self, store):
        self.store = store
        self.sync_calls = []

    def sync(self, symbol, *, timeframe, from_date, till_date):
        self.sync_calls.append((symbol, timeframe, from_date, till_date))

    def load_exact(self, instrument, *, timeframe, from_date, till_date):
        return self.store.read(instrument, timeframe, from_date, till_date)


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


def test_stored_view_reads_persisted_d1_candles():
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


@pytest.mark.parametrize("timeframe", ["H1", "M15"])
def test_intraday_bypasses_durable_store_and_uses_live_base(timeframe: str):
    store = DuckDBHistoricalCandleStore(":memory:")
    history = HistoricalDataService(market_data=SimpleNamespace(), store=store)
    base = LiveIntradayBase()
    view = StoredMarketDataView(base, history)

    series = view.candles_for_instrument(
        INST,
        timeframe=timeframe,
        from_date="2026-09-01",
        till_date="2026-09-02",
    )

    assert series.source == "LIVE_TEST"
    assert base.calls == [("SBER", timeframe, "2026-09-01", "2026-09-02")]
    assert store.coverage("SBER", timeframe).count == 0
    store.close()


def test_operational_analysis_refuses_to_invent_empty_d1_archive_start():
    store = DuckDBHistoricalCandleStore(":memory:")
    history = SpyHistory(store)
    view = StoredMarketDataView(Base(), history, ensure_daily_history=True)  # type: ignore[arg-type]

    with pytest.raises(DailyHistoryInitializationRequiredError, match="approved from_date"):
        view.candles_for_instrument(
            INST,
            timeframe="D1",
            from_date="2025-11-17",
            till_date="2026-09-13",
        )

    assert history.sync_calls == []
    store.close()


def test_operational_analysis_extends_existing_d1_forward_without_backfilling_earlier():
    store = DuckDBHistoricalCandleStore(":memory:")
    store.upsert_series(_d1(INST, "2026-01-15"))
    history = SpyHistory(store)
    view = StoredMarketDataView(Base(), history, ensure_daily_history=True)  # type: ignore[arg-type]

    view.candles_for_instrument(
        INST,
        timeframe="D1",
        from_date="2025-11-17",
        till_date="2026-09-13",
    )

    assert history.sync_calls == [("SBER", "D1", "2026-01-15", "2026-09-13")]
    store.close()


def test_frozen_direct_instrument_resolves_from_store_without_live_lookup():
    store = DuckDBHistoricalCandleStore(":memory:")
    store.upsert_series(_d1(INST, "2026-09-01"))
    history = HistoricalDataService(market_data=SimpleNamespace(), store=store)
    view = StoredMarketDataView(
        NoLiveResolveBase(),
        history,
        require_stored_resolution=True,
    )

    instrument = view.resolve("SBER", as_of=date(2026, 9, 1))

    assert instrument.secid == "SBER"
    store.close()


def test_frozen_direct_instrument_missing_metadata_fails_closed_without_live_lookup():
    store = DuckDBHistoricalCandleStore(":memory:")
    history = HistoricalDataService(market_data=SimpleNamespace(), store=store)
    view = StoredMarketDataView(
        NoLiveResolveBase(),
        history,
        require_stored_resolution=True,
    )

    with pytest.raises(RuntimeError, match="stored instrument metadata missing"):
        view.resolve("SBER", as_of=date(2026, 9, 1))
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
    view = StoredMarketDataView(
        NoLiveResolveBase(), history, require_stored_resolution=True
    )

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
    view = StoredMarketDataView(
        NoLiveResolveBase(), history, require_stored_resolution=True
    )

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
    view = StoredMarketDataView(
        NoLiveResolveBase(), history, require_stored_resolution=True
    )

    with pytest.raises(RuntimeError, match="stored futures session is not verified"):
        view.resolve("GOLD", as_of=date(2025, 1, 10))
    store.close()
