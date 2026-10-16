from datetime import date, timedelta

import pytest

from birzha.application.historical_data import (
    HistoricalDataIncompleteError,
    HistoricalDataService,
)
from birzha.application.warmup_session_evidence import (
    WarmupSessionEvidenceOrigin,
    read_verified_warmup_session_dates,
    warmup_session_evidence_key,
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

    warmup_dates = (date(2025, 1, 8), date(2025, 1, 9))
    evidence_key = warmup_session_evidence_key(
        secid=GOLD_NEXT.secid,
        expected_dates=warmup_dates,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    assert store.is_session_range_verified(
        evidence_key, "2025-01-08", "2025-01-09"
    ) is True
    assert store.stored_session_contracts(
        evidence_key, "2025-01-08", "2025-01-09"
    ) == (("2025-01-08", "GDM5"), ("2025-01-09", "GDM5"))
    assert read_verified_warmup_session_dates(
        store,
        evidence_key=evidence_key,
        secid="GDM5",
        from_date="2025-01-08",
        till_date="2025-01-09",
    ) == warmup_dates
    legacy_key = "GDM5#CONTRACT_WARMUP_V2_ACTIVITY"
    assert store.stored_sessions(legacy_key, "2025-01-08", "2025-01-09") == ()

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
    assert store.stored_session_contracts(
        evidence_key, "2025-01-08", "2025-01-09"
    ) == (("2025-01-08", "GDM5"), ("2025-01-09", "GDM5"))
    store.close()


class _ConfiguredCalendar:
    expected_dates: tuple[date, ...] = ()

    def __init__(self, provider):
        pass

    def dates(self, *, from_date, till_date, **kwargs):
        return tuple(
            day
            for day in self.expected_dates
            if from_date <= day <= till_date
        )


def test_warmup_date_change_creates_a_distinct_idempotent_generation(monkeypatch) -> None:
    import birzha.application.historical_data as module

    monkeypatch.setattr(module, "MoexTradingCalendar", _ConfiguredCalendar)
    store = DuckDBHistoricalCandleStore()
    service = HistoricalDataService(  # type: ignore[arg-type]
        market_data=_Market(), store=store
    )
    _ConfiguredCalendar.expected_dates = (date(2025, 1, 8), date(2025, 1, 9))
    service._sync_contract_warmup(
        GOLD_NEXT, timeframe="D1", active_start=ACTIVE_START
    )
    first_key = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=_ConfiguredCalendar.expected_dates,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )

    _ConfiguredCalendar.expected_dates = (
        date(2025, 1, 7),
        date(2025, 1, 8),
        date(2025, 1, 9),
    )
    service._sync_contract_warmup(
        GOLD_NEXT, timeframe="D1", active_start=ACTIVE_START
    )
    second_key = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=_ConfiguredCalendar.expected_dates,
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    assert first_key != second_key
    assert store.stored_session_contracts(
        first_key, "2025-01-08", "2025-01-09"
    ) == (("2025-01-08", "GDM5"), ("2025-01-09", "GDM5"))
    assert store.stored_session_contracts(
        second_key, "2025-01-07", "2025-01-09"
    ) == (
        ("2025-01-07", "GDM5"),
        ("2025-01-08", "GDM5"),
        ("2025-01-09", "GDM5"),
    )
    assert store.is_session_range_verified(
        first_key, "2025-01-08", "2025-01-09"
    ) is True
    assert store.is_session_range_verified(
        second_key, "2025-01-07", "2025-01-09"
    ) is True

    service._sync_contract_warmup(
        GOLD_NEXT, timeframe="D1", active_start=ACTIVE_START
    )
    assert store.stored_session_contracts(
        second_key, "2025-01-07", "2025-01-09"
    ) == (
        ("2025-01-07", "GDM5"),
        ("2025-01-08", "GDM5"),
        ("2025-01-09", "GDM5"),
    )
    store.close()


def test_legacy_warmup_marker_is_not_promoted_to_exact_session_evidence(monkeypatch) -> None:
    import birzha.application.historical_data as module

    monkeypatch.setattr(module, "MoexTradingCalendar", _Calendar)
    store = DuckDBHistoricalCandleStore()
    legacy_key = "GDM5#CONTRACT_WARMUP_V2_ACTIVITY"
    store.mark_verified(legacy_key, "D1", "2025-01-08", "2025-01-09")
    market = _Market()
    service = _RollingHistory(
        market_data=market, store=store
    )  # type: ignore[arg-type]

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
    assert store.is_verified(legacy_key, "D1", "2025-01-08", "2025-01-09") is True
    assert store.stored_sessions(legacy_key, "2025-01-08", "2025-01-09") == ()
    evidence_key = warmup_session_evidence_key(
        secid="GDM5",
        expected_dates=(date(2025, 1, 8), date(2025, 1, 9)),
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    assert store.is_session_range_verified(
        evidence_key, "2025-01-08", "2025-01-09"
    ) is True
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
    evidence_key = warmup_session_evidence_key(
        secid=GOLD_NEXT.secid,
        expected_dates=(date(2025, 1, 8), date(2025, 1, 9)),
        origin=WarmupSessionEvidenceOrigin.CAPTURED_AT_SYNC,
    )
    assert store.is_session_range_verified(
        evidence_key, "2025-01-08", "2025-01-09"
    ) is False
    assert store.stored_session_contracts(
        evidence_key, "2025-01-08", "2025-01-09"
    ) == ()
    store.close()
