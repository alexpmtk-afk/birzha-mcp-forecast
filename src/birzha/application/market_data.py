"""Application services for real market-data retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from birzha.domain.market import CandleSeries, Instrument
from birzha.providers.moex_iss import MoexIssClient


@dataclass(slots=True)
class MarketDataService:
    provider: MoexIssClient

    @classmethod
    def default(cls) -> "MarketDataService":
        return cls(provider=MoexIssClient())

    def resolve(self, symbol: str) -> Instrument:
        # First production slice: futures root resolution. Cross-asset routing is
        # added in MCP-M3B without changing the domain contract.
        return self.provider.resolve_active_future(symbol)

    def candles(
        self,
        symbol: str,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
        completed_only: bool = True,
    ) -> CandleSeries:
        instrument = self.resolve(symbol)
        return self.provider.fetch_candles(
            instrument,
            timeframe=timeframe,
            from_date=from_date,
            till_date=till_date,
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
        if lookback_days <= 0 or lookback_days > 3660:
            raise ValueError("lookback_days must be between 1 and 3660")
        till = date.today()
        start = till - timedelta(days=lookback_days)
        return self.candles(
            symbol,
            timeframe=timeframe,
            from_date=start.isoformat(),
            till_date=till.isoformat(),
            completed_only=completed_only,
        )
