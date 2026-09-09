"""Read-only market-data view backed by the persistent Historical Data store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from birzha.application.historical_data import HistoricalDataService
from birzha.application.market_data import MarketDataService
from birzha.domain.market import CandleSeries, Instrument


@dataclass(slots=True)
class StoredMarketDataView:
    base: MarketDataService
    history: HistoricalDataService

    @property
    def provider(self):
        return self.base.provider

    @property
    def direct_resolver(self):
        return self.base.direct_resolver

    @property
    def historical_future_resolver(self):
        return self.base.historical_future_resolver

    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        return self.base.resolve(symbol, as_of=as_of)

    def candles_for_instrument(
        self,
        instrument: Instrument,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
        completed_only: bool = True,
        now: datetime | None = None,
    ) -> CandleSeries:
        series=self.history.load_exact(
            instrument,timeframe=timeframe,from_date=from_date,till_date=till_date
        )
        candles=series.candles
        if completed_only:
            candles=tuple(item for item in candles if item.completed)
        return CandleSeries(
            instrument=instrument,
            timeframe=timeframe.upper(),
            candles=tuple(candles),
            source="HISTORICAL_STORE",
        )

    def candles(
        self,
        symbol: str,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
        completed_only: bool = True,
    ) -> CandleSeries:
        instrument=self.resolve(symbol,as_of=date.fromisoformat(till_date[:10]))
        return self.candles_for_instrument(
            instrument,timeframe=timeframe,from_date=from_date,till_date=till_date,
            completed_only=completed_only,
        )

    def recent_candles(
        self,
        symbol: str,
        *,
        timeframe: str,
        lookback_days: int,
        completed_only: bool = True,
    ) -> CandleSeries:
        till=datetime.now().date()
        start=till-timedelta(days=lookback_days)
        return self.candles(
            symbol,timeframe=timeframe,from_date=start.isoformat(),till_date=till.isoformat(),
            completed_only=completed_only,
        )
