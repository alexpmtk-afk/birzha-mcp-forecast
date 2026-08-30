"""Application services for real market-data retrieval."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.providers.moex_history import MoexHistoricalFutureResolver
from birzha.providers.moex_iss import MoexIssClient
from birzha.providers.moex_resolver import MoexDirectInstrumentResolver


MOEX_TIMEZONE = ZoneInfo("Europe/Moscow")


@dataclass(slots=True)
class MarketDataService:
    provider: MoexIssClient
    direct_resolver: MoexDirectInstrumentResolver | None = None
    historical_future_resolver: MoexHistoricalFutureResolver | None = None

    @classmethod
    def default(cls) -> "MarketDataService":
        provider = MoexIssClient()
        return cls(
            provider=provider,
            direct_resolver=MoexDirectInstrumentResolver(provider),
            historical_future_resolver=MoexHistoricalFutureResolver(provider),
        )

    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        """Resolve exact SECID first, otherwise the correct futures root contract.

        For historical dates, futures roots are resolved from the MOEX ISS
        history endpoint for that date. This is mandatory for causal backtests.
        """
        if self.direct_resolver is not None:
            direct = self.direct_resolver.resolve(symbol)
            if direct is not None and direct.asset_class != "unknown":
                return direct
        if as_of is not None and self.historical_future_resolver is not None:
            today = datetime.now(MOEX_TIMEZONE).date()
            if as_of < today:
                return self.historical_future_resolver.resolve(symbol, as_of)
        if as_of is None:
            return self.provider.resolve_active_future(symbol)
        return self.provider.resolve_active_future(symbol, as_of=as_of)

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
        raw = self.provider.fetch_candles(
            instrument,
            timeframe=timeframe,
            from_date=from_date,
            till_date=till_date,
            completed_only=False,
        )
        normalized = tuple(_normalize_completion(candle, now=now) for candle in raw.candles)
        if completed_only:
            normalized = tuple(candle for candle in normalized if candle.completed)
        return CandleSeries(
            instrument=raw.instrument,
            timeframe=raw.timeframe,
            candles=normalized,
            source=raw.source,
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
        as_of = date.fromisoformat(till_date[:10])
        instrument = self.resolve(symbol, as_of=as_of)
        return self.candles_for_instrument(
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
        till = datetime.now(MOEX_TIMEZONE).date()
        start = till - timedelta(days=lookback_days)
        return self.candles(
            symbol,
            timeframe=timeframe,
            from_date=start.isoformat(),
            till_date=till.isoformat(),
            completed_only=completed_only,
        )


def _normalize_completion(candle: Candle, *, now: datetime | None = None) -> Candle:
    observed_now = now or datetime.now(MOEX_TIMEZONE)
    if observed_now.tzinfo is None:
        observed_now = observed_now.replace(tzinfo=MOEX_TIMEZONE)
    else:
        observed_now = observed_now.astimezone(MOEX_TIMEZONE)
    try:
        end = datetime.fromisoformat(candle.end.replace("Z", "+00:00"))
    except ValueError:
        return replace(candle, completed=False)
    if end.tzinfo is None:
        end = end.replace(tzinfo=MOEX_TIMEZONE)
    else:
        end = end.astimezone(MOEX_TIMEZONE)
    completed = bool(candle.completed and end <= observed_now)
    return replace(candle, completed=completed)
