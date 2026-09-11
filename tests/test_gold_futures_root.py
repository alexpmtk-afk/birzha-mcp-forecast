from datetime import date

from birzha.application.historical_data import HistoricalDataService
from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.application.market_data import MarketDataService, is_futures_root_symbol
from birzha.application.validation import WalkForwardValidator
from birzha.domain.market import Instrument
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


GOLD_DIRECT = Instrument("GOLD", "GOLD", "RFUD", "futures", "forts", "future")
GDH5 = Instrument("GOLD", "GDH5", "RFUD", "futures", "forts", "future", root_symbol="GOLD")
GDM5 = Instrument("GOLD", "GDM5", "RFUD", "futures", "forts", "future", root_symbol="GOLD")


class _DirectResolver:
    def __init__(self):
        self.calls = 0

    def resolve(self, symbol):
        self.calls += 1
        return GOLD_DIRECT


class _HistoricalResolver:
    def __init__(self):
        self.calls = 0
        self.resolve_calls = 0

    def timeline(self, symbol, start, finish):
        self.calls += 1
        assert symbol == "GOLD"
        return (
            (date(2025, 1, 1), GDH5),
            (date(2025, 1, 2), GDH5),
            (date(2025, 1, 3), GDM5),
        )

    def resolve(self, symbol, as_of):
        self.resolve_calls += 1
        assert symbol == "GOLD"
        return GDH5 if as_of <= date(2025, 1, 2) else GDM5


class _GoldCalendar:
    def dates(self, *, security, from_date, till_date, **kwargs):
        if security == "GDH5":
            candidates = (date(2025, 1, 1), date(2025, 1, 2))
        elif security == "GDM5":
            candidates = (date(2025, 1, 3),)
        else:
            raise AssertionError(f"unexpected security: {security}")
        return tuple(day for day in candidates if from_date <= day <= till_date)


class _NoopAnalytics:
    pass


def _market(direct, history):
    return MarketDataService(
        provider=object(),  # type: ignore[arg-type]
        direct_resolver=direct,  # type: ignore[arg-type]
        historical_future_resolver=history,  # type: ignore[arg-type]
    )


def test_gold_is_explicit_rolling_futures_root() -> None:
    assert is_futures_root_symbol("GOLD") is True
    assert is_futures_root_symbol("gold") is True
    assert is_futures_root_symbol("SBER") is False


def test_gold_historical_segments_ignore_colliding_direct_secid() -> None:
    direct = _DirectResolver()
    history = _HistoricalResolver()
    service = HistoricalDataService(
        market_data=_market(direct, history),
        store=DuckDBHistoricalCandleStore(),
    )

    segments = service._segments("GOLD", date(2025, 1, 1), date(2025, 1, 3))

    assert direct.calls == 0
    assert history.calls == 1
    assert [(item.secid, left, right) for item, left, right in segments] == [
        ("GDH5", date(2025, 1, 1), date(2025, 1, 2)),
        ("GDM5", date(2025, 1, 3), date(2025, 1, 3)),
    ]


def test_gold_historical_flow_uses_same_contract_chain() -> None:
    direct = _DirectResolver()
    history = _HistoricalResolver()
    store = DuckDBHistoricalFlowStore(":memory:")
    service = HistoricalFlowDataService(
        market_data=_market(direct, history),
        analytics=_NoopAnalytics(),  # type: ignore[arg-type]
        store=store,
    )

    segments = service._segments("GOLD", date(2025, 1, 1), date(2025, 1, 3))

    assert direct.calls == 0
    assert history.calls == 1
    assert [(item.secid, left, right) for item, left, right in segments] == [
        ("GDH5", date(2025, 1, 1), date(2025, 1, 2)),
        ("GDM5", date(2025, 1, 3), date(2025, 1, 3)),
    ]
    store.close()


def test_gold_walk_forward_calendar_bypasses_colliding_direct_secid() -> None:
    direct = _DirectResolver()
    history = _HistoricalResolver()
    validator = WalkForwardValidator(
        market_data=_market(direct, history),
        forecasts=object(),  # type: ignore[arg-type]
        calendar=_GoldCalendar(),  # type: ignore[arg-type]
    )

    sessions = validator._session_dates(
        "GOLD", start=date(2025, 1, 1), end=date(2025, 1, 3)
    )

    assert direct.calls == 0
    assert history.resolve_calls == 2
    assert sessions == (
        date(2025, 1, 1),
        date(2025, 1, 2),
        date(2025, 1, 3),
    )
