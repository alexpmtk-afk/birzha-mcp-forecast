"""Historical Data Foundation: persistent coverage and incremental tail loading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from birzha.application.market_data import MarketDataService
from birzha.domain.market import CandleSeries
from birzha.storage.historical_store import DuckDBHistoricalCandleStore, HistoricalCoverage


@dataclass(frozen=True, slots=True)
class HistoricalSyncResult:
    symbol: str
    secid: str
    timeframe: str
    requested_from: str
    requested_till: str
    fetched_ranges: tuple[tuple[str, str], ...]
    fetched_candles: int
    stored_candles: int
    coverage: HistoricalCoverage

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "secid": self.secid,
            "timeframe": self.timeframe,
            "requested_from": self.requested_from,
            "requested_till": self.requested_till,
            "fetched_ranges": [list(item) for item in self.fetched_ranges],
            "fetched_candles": self.fetched_candles,
            "stored_candles": self.stored_candles,
            "coverage": self.coverage.to_dict(),
        }


@dataclass(slots=True)
class HistoricalDataService:
    market_data: MarketDataService
    store: DuckDBHistoricalCandleStore

    def sync(
        self,
        symbol: str,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
    ) -> HistoricalSyncResult:
        start = date.fromisoformat(from_date[:10])
        finish = date.fromisoformat(till_date[:10])
        if finish < start:
            raise ValueError("till_date must be on or after from_date")

        instrument = self.market_data.resolve(symbol, as_of=finish)
        before = self.store.coverage(instrument.secid, timeframe)
        ranges = _missing_tail_ranges(start, finish, before)
        fetched = 0

        for left, right in ranges:
            series = self.market_data.candles_for_instrument(
                instrument,
                timeframe=timeframe,
                from_date=left.isoformat(),
                till_date=right.isoformat(),
                completed_only=True,
            )
            fetched += series.count
            self.store.upsert_series(series)

        coverage = self.store.coverage(instrument.secid, timeframe)
        stored = self.store.read(instrument, timeframe, from_date, till_date)
        return HistoricalSyncResult(
            symbol=symbol,
            secid=instrument.secid,
            timeframe=timeframe,
            requested_from=from_date,
            requested_till=till_date,
            fetched_ranges=tuple((left.isoformat(), right.isoformat()) for left, right in ranges),
            fetched_candles=fetched,
            stored_candles=stored.count,
            coverage=coverage,
        )

    def load(
        self,
        symbol: str,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
        sync_first: bool = True,
    ) -> CandleSeries:
        finish = date.fromisoformat(till_date[:10])
        instrument = self.market_data.resolve(symbol, as_of=finish)
        if sync_first:
            self.sync(symbol, timeframe=timeframe, from_date=from_date, till_date=till_date)
        return self.store.read(instrument, timeframe, from_date, till_date)


def _missing_tail_ranges(
    requested_from: date,
    requested_till: date,
    coverage: HistoricalCoverage,
) -> tuple[tuple[date, date], ...]:
    """Return only uncovered left/right calendar tails.

    Internal exchange-session gap detection belongs to the next coverage-index
    increment, where the MOEX trading calendar can distinguish a true hole from
    weekends/holidays. This first increment deliberately does not invent gaps.
    """
    if coverage.count == 0 or coverage.first_begin is None or coverage.last_end is None:
        return ((requested_from, requested_till),)

    first = date.fromisoformat(coverage.first_begin[:10])
    last = date.fromisoformat(coverage.last_end[:10])
    missing: list[tuple[date, date]] = []
    if requested_from < first:
        missing.append((requested_from, min(requested_till, first - timedelta(days=1))))
    if requested_till > last:
        missing.append((max(requested_from, last + timedelta(days=1)), requested_till))
    return tuple(item for item in missing if item[0] <= item[1])
