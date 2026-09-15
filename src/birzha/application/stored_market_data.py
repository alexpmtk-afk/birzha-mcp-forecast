"""Layered market-data view: durable D1 plus on-demand intraday data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from birzha.application.historical_data import HistoricalDataService, _verification_symbol
from birzha.application.market_data import MarketDataService, is_futures_root_symbol
from birzha.domain.market import CandleSeries, Instrument


class DailyHistoryInitializationRequiredError(RuntimeError):
    """Durable D1 has not been initialized with an explicit user/project period."""


@dataclass(slots=True)
class StoredMarketDataView:
    """Use shared history only for D1; route intraday directly to MOEX.

    ``require_stored_resolution`` is used by frozen validation: contract identity
    must come from the already verified D1 session map and no live resolver may
    silently change it. ``ensure_daily_history`` may extend an already existing
    D1 archive forward, but it must never invent/extend its historical start.

    H1/M15 are never read from or written to durable candle history by this
    view. They are fetched from ``base`` for the requested analysis window.
    """

    base: MarketDataService
    history: HistoricalDataService
    require_stored_resolution: bool = False
    ensure_daily_history: bool = False

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
        if self.require_stored_resolution:
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
            raise RuntimeError(
                f"stored instrument metadata missing for frozen validation: {symbol}"
            )

        return self.base.resolve(symbol, as_of=as_of)

    def _existing_daily_start(self, instrument: Instrument, *, till_date: str) -> str:
        """Return pre-existing durable D1 start without inventing a new one."""
        root = instrument.root_symbol or instrument.symbol
        if instrument.root_symbol and is_futures_root_symbol(root):
            session_symbol = _verification_symbol(root, "D1", is_root=True)
            contracts = self.history.store.stored_session_contracts(
                session_symbol,
                "1900-01-01",
                till_date[:10],
            )
            if contracts:
                return contracts[0][0][:10]

        coverage = self.history.store.coverage(instrument.secid, "D1")
        if coverage.count > 0 and coverage.first_begin:
            return coverage.first_begin[:10]
        raise DailyHistoryInitializationRequiredError(
            f"durable D1 history is not initialized for {root}; "
            "run explicit D1 history sync with the approved from_date first"
        )

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
        tf = timeframe.upper()
        if tf != "D1":
            return self.base.candles_for_instrument(
                instrument,
                timeframe=tf,
                from_date=from_date,
                till_date=till_date,
                completed_only=completed_only,
                now=now,
            )

        if self.ensure_daily_history:
            persistent_symbol = instrument.root_symbol or instrument.symbol
            existing_start = date.fromisoformat(
                self._existing_daily_start(instrument, till_date=till_date)
            )
            requested_start = date.fromisoformat(from_date[:10])
            # Never backfill earlier than the explicitly initialized archive.
            sync_start = max(existing_start, requested_start)
            self.history.sync(
                persistent_symbol,
                timeframe="D1",
                from_date=sync_start.isoformat(),
                till_date=till_date,
            )

        series = self.history.load_exact(
            instrument,
            timeframe="D1",
            from_date=from_date,
            till_date=till_date,
        )
        candles = series.candles
        if completed_only:
            candles = tuple(item for item in candles if item.completed)
        return CandleSeries(
            instrument=instrument,
            timeframe="D1",
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
        instrument = self.resolve(
            symbol, as_of=date.fromisoformat(till_date[:10])
        )
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
        till = datetime.now().date()
        start = till - timedelta(days=lookback_days)
        return self.candles(
            symbol,
            timeframe=timeframe,
            from_date=start.isoformat(),
            till_date=till.isoformat(),
            completed_only=completed_only,
        )
