from datetime import date

from birzha.application.historical_data import HistoricalDataService
from birzha.application.market_data import MarketDataService, is_futures_root_symbol
from birzha.domain.market import Instrument
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

    def timeline(self, symbol, start, finish):
        self.calls += 1
        assert symbol == "GOLD"
        return (
            (date(2025, 1, 1), GDH5),
            (date(2025, 1, 2), GDH5),
            (date(2025, 1, 3), GDM5),
        )


def test_gold_is_explicit_rolling_futures_root() -> None:
    assert is_futures_root_symbol("GOLD") is True
    assert is_futures_root_symbol("gold") is True
    assert is_futures_root_symbol("SBER") is False


def test_gold_historical_segments_ignore_colliding_direct_secid() -> None:
    direct = _DirectResolver()
    history = _HistoricalResolver()
    market = MarketDataService(
        provider=object(),  # type: ignore[arg-type]
        direct_resolver=direct,  # type: ignore[arg-type]
        historical_future_resolver=history,  # type: ignore[arg-type]
    )
    service = HistoricalDataService(market_data=market, store=DuckDBHistoricalCandleStore())

    segments = service._segments("GOLD", date(2025, 1, 1), date(2025, 1, 3))

    assert direct.calls == 0
    assert history.calls == 1
    assert [(item.secid, left, right) for item, left, right in segments] == [
        ("GDH5", date(2025, 1, 1), date(2025, 1, 2)),
        ("GDM5", date(2025, 1, 3), date(2025, 1, 3)),
    ]
