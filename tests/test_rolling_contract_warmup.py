from datetime import date, timedelta

import pytest

from birzha.application.historical_data import (
    HistoricalDataIncompleteError,
    HistoricalDataService,
)
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


ACTIVE_START = date(2025, 1, 10)
GOLD_NEXT = Instrument(
    symbol="GOLD",
    secid="GDM5",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="GOLD",
)


class _Calendar:
    def __init__(self, provider):
        pass

    def dates(self, *, from_date, till_date, **kwargs):
        candidates = (
            date(2025, 1, 8),
            date(2025, 1, 9),
            date(2025, 1, 10),
            date(2025, 1, 11),
            date(2025, 1, 12),
        )
        return tuple(day for day in candidates if from_date <= day <= till_date)


class _Market:
    provider = object()
    direct_resolver = object()

    def __init__(self):
        self.fetches: list[tuple[str, str, str]] = []

    def candles_for_instrument(
        self,
        instrument,
        *,
        timeframe,
        from_date,
        till_date,
        completed_only=True,
    ):
        self.fetches.append((timeframe, from_date, till_date))
        start = date.fromisoformat(from_date)
        finish = date.fromisoformat(till_date)
        candles = []
        cursor = start
        while cursor <= finish:
            candles.append(
                Candle(
                    100.0,
                    101.0,
                    102.0,
                    99.0,
                    1000.0,
                    10.0,
                    f"{cursor.isoformat()} 10:00:00",
                    f"{cursor.isoformat()} 23:59:59",
                )
            )
            cursor += timedelta(days=1)
        return CandleSeries(
            instrument=instrument,
            timeframe=timeframe,
            candles=tuple(candles),
            source="TEST",
        )


class _IncompleteWarmupMarket(_Market):
    def candles_for_instrument(
        self,
        instrument,
        *,
        timeframe,
        from_date,
        till_date,
        completed_only=True,
    ):
        series = super().candles_for_instrument(
            instrument,
            timeframe=timeframe,
            from_date=from_date,
            till_date=till_date,
            completed_only=completed_only,
        )
        if from_date <= "2025-01-09" <= till_date and till_date < "2025-01-10":
            return CandleSeries(
                instrument=instrument,
                timeframe=timeframe,
                candles=tuple(
                    candle
                    for candle in series.candles
                    if candle.begin[:10] != "2025-01-09"
                ),
                source="TEST",
            )
        return series


class _RollingHistory(HistoricalDataService):
    def _segments(self, symbol, start, finish):
        assert symbol == "GOLD"
        return ((GOLD_NEXT, ACTIVE_START, finish),)


def test_rolling_contract_warmup_is_stored_but_not_added_to_root_sessions(monkeypatch) -> None:
    import birzha.application.historical_data as module

    monkeypatch.setattr(module, "MoexTradingCalendar", _Calendar)
    store = DuckDBHistoricalCandleStore()
    market = _Market()
    service = _RollingHistory(market_data=market, store=store)  # type: ignore[arg-type]

    service.sync(
        "GOLD",
        timeframe="D1",
        from_date="2025-01-01",
        till_date="2025-01-11",
    )

    assert market.fetches == [
        ("D1", "2025-01-08", "2025-01-09"),
        ("D1", "2025-01-10", "2025-01-11"),
    ]
    assert store.read(GOLD_NEXT, "D1", "2025-01-08", "2025-01-11").count == 4
    assert tuple(
        day.isoformat()
        for day in service.session_dates(
            "GOLD", from_date="2025-01-01", till_date="2025-01-11"
        )
    ) == ("2025-01-10", "2025-01-11")

    # Extending the range reuses already stored preroll days and fetches only
    # the newly active session.
    service.sync(
        "GOLD",
        timeframe="D1",
        from_date="2025-01-01",
        till_date="2025-01-12",
    )
    assert market.fetches[-1] == ("D1", "2025-01-12", "2025-01-12")
    assert market.fetches.count(("D1", "2025-01-08", "2025-01-09")) == 1
    assert tuple(
        day.isoformat()
        for day in service.session_dates(
            "GOLD", from_date="2025-01-01", till_date="2025-01-12"
        )
    ) == ("2025-01-10", "2025-01-11", "2025-01-12")
    store.close()


def test_incomplete_preroll_response_blocks_root_readiness(monkeypatch) -> None:
    import birzha.application.historical_data as module

    monkeypatch.setattr(module, "MoexTradingCalendar", _Calendar)
    store = DuckDBHistoricalCandleStore()
    service = _RollingHistory(  # type: ignore[arg-type]
        market_data=_IncompleteWarmupMarket(),
        store=store,
    )

    with pytest.raises(
        HistoricalDataIncompleteError,
        match="contract warmup provider response incomplete.*GDM5 D1.*2025-01-09",
    ):
        service.sync(
            "GOLD",
            timeframe="D1",
            from_date="2025-01-01",
            till_date="2025-01-11",
        )

    assert service.is_range_verified(
        "GOLD",
        timeframe="D1",
        from_date="2025-01-01",
        till_date="2025-01-11",
    ) is False
    store.close()


def test_warmup_keys_are_immutable_versioned_and_origin_separated() -> None:
    from birzha.application.warmup_session_evidence import warmup_d1_evidence_key

    days = ("2025-01-08", "2025-01-09")
    captured = warmup_d1_evidence_key("GDM5", days)
    assert captured == warmup_d1_evidence_key("GDM5", tuple(reversed(days)))
    assert captured != warmup_d1_evidence_key("GDM5", ("2025-01-08",))
    assert captured != warmup_d1_evidence_key("GDH5", days)
    assert captured != warmup_d1_evidence_key(
        "GDM5", days, origin="RECONSTRUCTED_MOEX"
    )
    assert "#CAPTURED_AT_SYNC#" in captured
    with pytest.raises(ValueError, match="duplicate"):
        warmup_d1_evidence_key("GDM5", ("2025-01-08", "2025-01-08"))
    with pytest.raises(ValueError, match="ISO"):
        warmup_d1_evidence_key("GDM5", ("not-a-date",))


def test_d1_warmup_records_exact_dates_in_isolated_verified_generation(monkeypatch) -> None:
    import birzha.application.historical_data as module
    from birzha.application.warmup_session_evidence import warmup_d1_evidence_key

    monkeypatch.setattr(module, "MoexTradingCalendar", _Calendar)
    store = DuckDBHistoricalCandleStore()
    service = _RollingHistory(market_data=_Market(), store=store)  # type: ignore[arg-type]
    root_key = (
        "GOLD#ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V2_ACTIVITY"
    )
    store.record_sessions(root_key, GOLD_NEXT.secid, ("2025-01-10",))
    before_root = store.stored_session_contracts(root_key, "2025-01-01", "2025-01-12")
    service._sync_contract_warmup(GOLD_NEXT, timeframe="D1", active_start=ACTIVE_START)
    key = warmup_d1_evidence_key("GDM5", ("2025-01-08", "2025-01-09"))
    assert store.stored_session_contracts(key, "2025-01-01", "2025-01-12") == (
        ("2025-01-08", "GDM5"),
        ("2025-01-09", "GDM5"),
    )
    assert store.is_session_range_verified(key, "2025-01-08", "2025-01-09")
    assert store.stored_session_contracts(root_key, "2025-01-01", "2025-01-12") == before_root
    assert store.stored_sessions("GOLD", "2025-01-01", "2025-01-12") == ()
    assert store.stored_sessions("GDM5#CONTRACT_WARMUP_V2_ACTIVITY",
                                 "2025-01-01", "2025-01-12") == ()
    # Repeat does not create a different generation.
    service._sync_contract_warmup(GOLD_NEXT, timeframe="D1", active_start=ACTIVE_START)
    assert store.stored_session_contracts(key, "2025-01-01", "2025-01-12") == (
        ("2025-01-08", "GDM5"),
        ("2025-01-09", "GDM5"),
    )
    store.close()


def test_generation_keys_allow_different_calendars_to_coexist() -> None:
    from birzha.application.warmup_session_evidence import warmup_d1_evidence_key

    store = DuckDBHistoricalCandleStore()
    first = ("2025-01-08", "2025-01-09")
    second = ("2025-01-08",)
    for dates in (first, second):
        key = warmup_d1_evidence_key("GDM5", dates)
        store.record_sessions(key, "GDM5", dates)
        store.mark_session_range_verified(key, dates[0], dates[-1])
    assert store.stored_sessions(warmup_d1_evidence_key("GDM5", first),
                                 "2025-01-01", "2025-01-12") == first
    assert store.stored_sessions(warmup_d1_evidence_key("GDM5", second),
                                 "2025-01-01", "2025-01-12") == second
    store.close()


def test_marker_only_legacy_warmup_is_not_relabelled_as_captured(monkeypatch) -> None:
    import birzha.application.historical_data as module
    from birzha.application.warmup_session_evidence import warmup_d1_evidence_key

    monkeypatch.setattr(module, "MoexTradingCalendar", _Calendar)
    store = DuckDBHistoricalCandleStore()
    store.mark_verified(
        "GDM5#CONTRACT_WARMUP_V2_ACTIVITY",
        "D1", "2025-01-08", "2025-01-09"
    )
    service = _RollingHistory(market_data=_Market(), store=store)  # type: ignore[arg-type]
    service._sync_contract_warmup(GOLD_NEXT, timeframe="D1", active_start=ACTIVE_START)
    key = warmup_d1_evidence_key("GDM5", ("2025-01-08", "2025-01-09"))
    assert not store.is_session_range_verified(key, "2025-01-08", "2025-01-09")
    assert store.stored_sessions(key, "2025-01-01", "2025-01-12") == ()
    store.close()


def test_incomplete_warmup_never_creates_verified_exact_dates(monkeypatch) -> None:
    import birzha.application.historical_data as module
    from birzha.application.warmup_session_evidence import warmup_d1_evidence_key

    monkeypatch.setattr(module, "MoexTradingCalendar", _Calendar)
    store = DuckDBHistoricalCandleStore()
    service = _RollingHistory(
        market_data=_IncompleteWarmupMarket(), store=store  # type: ignore[arg-type]
    )
    with pytest.raises(HistoricalDataIncompleteError):
        service._sync_contract_warmup(GOLD_NEXT, timeframe="D1", active_start=ACTIVE_START)
    key = warmup_d1_evidence_key("GDM5", ("2025-01-08", "2025-01-09"))
    assert store.stored_sessions(key, "2025-01-01", "2025-01-12") == ()
    assert not store.is_session_range_verified(key, "2025-01-08", "2025-01-09")
    store.close()


@pytest.mark.parametrize("issue", ["incomplete", "missing_ohlc"])
def test_bad_warmup_candle_has_no_verified_session_evidence(monkeypatch, issue) -> None:
    from dataclasses import replace
    import birzha.application.historical_data as module
    from birzha.application.warmup_session_evidence import warmup_d1_evidence_key

    class _BadWarmupMarket(_Market):
        def candles_for_instrument(self, instrument, *, timeframe, from_date,
                                   till_date, completed_only=True):
            series = super().candles_for_instrument(
                instrument, timeframe=timeframe, from_date=from_date,
                till_date=till_date, completed_only=completed_only
            )
            candles = tuple(
                replace(candle, completed=False)
                if issue == "incomplete" and candle.begin.startswith("2025-01-09")
                else replace(candle, high=None)
                if issue == "missing_ohlc" and candle.begin.startswith("2025-01-09")
                else candle
                for candle in series.candles
            )
            return CandleSeries(
                instrument=instrument, timeframe=timeframe, candles=candles, source="TEST"
            )

    monkeypatch.setattr(module, "MoexTradingCalendar", _Calendar)
    store = DuckDBHistoricalCandleStore()
    service = _RollingHistory(market_data=_BadWarmupMarket(), store=store)  # type: ignore[arg-type]
    with pytest.raises(HistoricalDataIncompleteError, match="D1 values incomplete"):
        service._sync_contract_warmup(GOLD_NEXT, timeframe="D1", active_start=ACTIVE_START)
    key = warmup_d1_evidence_key("GDM5", ("2025-01-08", "2025-01-09"))
    assert not store.is_session_range_verified(key, "2025-01-08", "2025-01-09")
    store.close()
