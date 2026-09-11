from datetime import date

from birzha.application.historical_data import (
    HistoricalDataService,
    M15_FULL_VERIFICATION_VERSION,
)
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


SBER = Instrument(
    symbol="SBER",
    secid="SBER",
    board="TQBR",
    engine="stock",
    market="shares",
    asset_class="equity",
)


class _DirectResolver:
    def resolve(self, symbol):
        assert symbol == "SBER"
        return SBER


class _Calendar:
    def __init__(self, provider):
        pass

    def dates(self, **kwargs):
        return (date(2026, 9, 1), date(2026, 9, 2))


class _Market:
    provider = object()
    direct_resolver = _DirectResolver()
    historical_future_resolver = None

    def __init__(self):
        self.fetches = []

    def candles_for_instrument(
        self, instrument, *, timeframe, from_date, till_date, completed_only=True
    ):
        self.fetches.append((from_date, till_date))
        candles = (
            Candle(100, 101, 102, 99, 1000, 10, "2026-09-01 10:00:00", "2026-09-01 10:14:59"),
            Candle(101, 102, 103, 100, 1100, 11, "2026-09-01 10:15:00", "2026-09-01 10:29:59"),
            Candle(102, 103, 104, 101, 1200, 12, "2026-09-02 10:00:00", "2026-09-02 10:14:59"),
            Candle(103, 104, 105, 102, 1300, 13, "2026-09-02 10:15:00", "2026-09-02 10:29:59"),
        )
        return CandleSeries(instrument=instrument, timeframe=timeframe, candles=candles)


def test_legacy_m15_marker_and_partial_day_do_not_skip_full_refetch(monkeypatch) -> None:
    import birzha.application.historical_data as module

    monkeypatch.setattr(module, "MoexTradingCalendar", _Calendar)
    store = DuckDBHistoricalCandleStore()
    store.upsert_series(
        CandleSeries(
            instrument=SBER,
            timeframe="M15",
            candles=(
                Candle(90, 91, 92, 89, 900, 9, "2026-09-01 10:00:00", "2026-09-01 10:14:59"),
            ),
        )
    )
    store.mark_verified("SBER", "M15", "2026-09-01", "2026-09-02")
    market = _Market()
    service = HistoricalDataService(market_data=market, store=store)  # type: ignore[arg-type]

    first = service.sync(
        "SBER", timeframe="M15", from_date="2026-09-01", till_date="2026-09-02"
    )

    assert first.reused_verified_range is False
    assert market.fetches == [("2026-09-01", "2026-09-02")]
    assert store.read(SBER, "M15", "2026-09-01", "2026-09-02").count == 4
    verification_key = f"SBER#{M15_FULL_VERIFICATION_VERSION}"
    assert store.is_verified(verification_key, "M15", "2026-09-01", "2026-09-02") is True

    second = service.sync(
        "SBER", timeframe="M15", from_date="2026-09-01", till_date="2026-09-02"
    )
    assert second.reused_verified_range is True
    assert market.fetches == [("2026-09-01", "2026-09-02")]
