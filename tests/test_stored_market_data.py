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



def test_duckdb_round_trips_causal_metadata_and_preserves_first_observation():
    store = DuckDBHistoricalCandleStore(":memory:")
    first = CandleSeries(
        INST,
        "H1",
        (
            Candle(
                100, 101, 102, 99, 1000, 10,
                "2026-09-01T10:00:00", "2026-09-01T10:59:59", True,
                available_at="2026-09-01T10:59:59+03:00",
                available_at_confidence="INFERRED",
                observed_at="2026-09-01T11:00:05+03:00",
                source="MOEX_ISS",
            ),
        ),
    )
    store.upsert_series(first)

    second = CandleSeries(
        INST,
        "H1",
        (
            Candle(
                100, 101, 102, 99, 1000, 10,
                "2026-09-01T10:00:00", "2026-09-01T10:59:59", True,
                available_at="2026-09-01T11:00:10+03:00",
                available_at_confidence="INFERRED",
                observed_at="2026-09-02T12:00:00+03:00",
                source="MOEX_ISS",
            ),
        ),
    )
    store.upsert_series(second)

    loaded = store.read(INST, "H1", "2026-09-01", "2026-09-01")
    candle = loaded.candles[0]
    assert candle.available_at == "2026-09-01T10:59:59+03:00"
    assert candle.observed_at == "2026-09-01T11:00:05+03:00"
    assert candle.available_at_confidence == "INFERRED"
    assert candle.source == "MOEX_ISS"
    store.close()



def test_candle_first_seen_value_is_immutable_and_revision_is_recorded():
    store = DuckDBHistoricalCandleStore(":memory:")
    first = CandleSeries(
        INST,
        "H1",
        (
            Candle(
                100, 101, 102, 99, 1000, 10,
                "2026-09-01T10:00:00", "2026-09-01T10:59:59", True,
                available_at="2026-09-01T10:59:59+03:00",
                observed_at="2026-09-01T11:00:05+03:00",
                source="MOEX_ISS",
            ),
        ),
    )
    revised = CandleSeries(
        INST,
        "H1",
        (
            Candle(
                100, 999, 1000, 99, 5000, 50,
                "2026-09-01T10:00:00", "2026-09-01T10:59:59", True,
                available_at="2026-09-01T10:59:59+03:00",
                observed_at="2026-09-02T12:00:00+03:00",
                source="MOEX_ISS",
            ),
        ),
    )

    store.upsert_series(first)
    store.upsert_series(revised)

    loaded = store.read(INST, "H1", "2026-09-01", "2026-09-01")
    assert loaded.candles[0].close == 101
    assert loaded.candles[0].volume == 10
    revision_count = store._connection.execute(
        "SELECT count(*) FROM historical_candle_revisions"
    ).fetchone()[0]
    assert revision_count == 1
    store.close()



def test_instrument_contract_metadata_round_trips_through_duckdb():
    instrument = Instrument(
        symbol="Si",
        secid="SiU6",
        board="RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        root_symbol="Si",
        last_trade_date="2026-09-17",
        currency="RUB",
        tick_size=1.0,
        tick_value=1.0,
        contract_multiplier=1000.0,
        expiration_date="2026-09-17",
        settlement_date="2026-09-18",
        calendar_id="MOEX:futures:forts:RFUD",
        session_profile="MOEX_SECURITY_CALENDAR",
        data_capabilities=("CANDLES", "TRADES", "OPEN_INTEREST"),
        roll_policy="LIQUID_CONTRACT_CAUSAL",
    )
    store = DuckDBHistoricalCandleStore(":memory:")
    store.upsert_series(_d1(instrument, "2026-09-01"))

    loaded = store.stored_instrument("SiU6")

    assert loaded is not None
    assert loaded.currency == "RUB"
    assert loaded.tick_size == 1.0
    assert loaded.tick_value == 1.0
    assert loaded.contract_multiplier == 1000.0
    assert loaded.expiration_date == "2026-09-17"
    assert loaded.settlement_date == "2026-09-18"
    assert loaded.calendar_id == "MOEX:futures:forts:RFUD"
    assert loaded.session_profile == "MOEX_SECURITY_CALENDAR"
    assert loaded.data_capabilities == ("CANDLES", "TRADES", "OPEN_INTEREST")
    assert loaded.roll_policy == "LIQUID_CONTRACT_CAUSAL"
    store.close()
