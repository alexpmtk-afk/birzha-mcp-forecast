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
