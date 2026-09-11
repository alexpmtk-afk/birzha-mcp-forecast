"""Read-only market-data view backed by the persistent Historical Data store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.market_data import MarketDataService, is_futures_root_symbol
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

    def stored_instrument(self, secid: str) -> Instrument | None:
        return self.history.store.stored_instrument(secid)

    def resolve(self, symbol: str, *, as_of: date | None = None) -> Instrument:
        if as_of is not None and is_futures_root_symbol(symbol):
            trade_date = as_of.isoformat()
            session_symbol = _verification_symbol(symbol, "D1", is_root=True)
            if not self.history.store.is_session_range_verified(
                session_symbol, trade_date, trade_date
            ):
                raise RuntimeError(
                    f"stored futures session is not verified for {symbol} {trade_date}"
                )
            secids = self.history.store.stored_session_secids(
                session_symbol, trade_date
            )
            if len(secids) != 1:
                raise RuntimeError(
                    f"stored futures session must resolve to exactly one contract for "
                    f"{symbol} {trade_date}; found={secids}"
                )
            instrument = self.stored_instrument(secids[0])
            if instrument is None:
                raise RuntimeError(
                    f"stored instrument metadata missing for {symbol} {trade_date}: "
                    f"{secids[0]}"
                )
            root = (instrument.root_symbol or instrument.symbol).upper()
            if root != symbol.upper():
                raise RuntimeError(
                    f"stored contract root mismatch for {symbol} {trade_date}: "
                    f"{instrument.secid}/{root}"
                )
            return instrument

        stored = self.stored_instrument(symbol)
        if stored is not None:
            return stored
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
