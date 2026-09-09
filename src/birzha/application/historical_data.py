"""Historical Data Foundation: durable coverage, gap repair and rollover-safe sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.application.market_data import MarketDataService
from birzha.domain.market import CandleSeries, Instrument
from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.storage.historical_store import HistoricalCandleStore, HistoricalCoverage


@dataclass(frozen=True, slots=True)
class ContractSyncResult:
    secid: str
    from_date: str
    till_date: str
    fetched_ranges: tuple[tuple[str, str], ...]
    fetched_candles: int
    stored_candles: int
    coverage: HistoricalCoverage

    def to_dict(self) -> dict[str, object]:
        return {
            "secid": self.secid,
            "from_date": self.from_date,
            "till_date": self.till_date,
            "fetched_ranges": [list(item) for item in self.fetched_ranges],
            "fetched_candles": self.fetched_candles,
            "stored_candles": self.stored_candles,
            "coverage": self.coverage.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class HistoricalSyncResult:
    symbol: str
    timeframe: str
    requested_from: str
    requested_till: str
    contracts: tuple[ContractSyncResult, ...]
    reused_verified_range: bool = False

    @property
    def fetched_candles(self) -> int:
        return sum(item.fetched_candles for item in self.contracts)

    @property
    def stored_candles(self) -> int:
        return sum(item.stored_candles for item in self.contracts)

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "requested_from": self.requested_from,
            "requested_till": self.requested_till,
            "contract_count": len(self.contracts),
            "reused_verified_range": self.reused_verified_range,
            "fetched_candles": self.fetched_candles,
            "stored_candles": self.stored_candles,
            "contracts": [item.to_dict() for item in self.contracts],
        }


@dataclass(slots=True)
class HistoricalDataService:
    market_data: MarketDataService
    store: HistoricalCandleStore

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

        if self.store.is_verified(symbol, timeframe, from_date[:10], till_date[:10]):
            return HistoricalSyncResult(symbol=symbol, timeframe=timeframe, requested_from=from_date, requested_till=till_date, contracts=(), reused_verified_range=True)

        segments = self._segments(symbol, start, finish)
        results = tuple(
            self._sync_contract(instrument, timeframe, left, right)
            for instrument, left, right in segments
        )
        self.store.mark_verified(symbol, timeframe, from_date[:10], till_date[:10])
        return HistoricalSyncResult(symbol=symbol, timeframe=timeframe, requested_from=from_date, requested_till=till_date, contracts=results)

    def sync_many(
        self, symbols: list[str], timeframes: list[str], *, from_date: str, till_date: str
    ) -> dict[str, object]:
        if not symbols or not timeframes:
            raise ValueError("symbols and timeframes must be non-empty")
        items: list[dict[str, object]] = []
        for symbol in symbols:
            for timeframe in timeframes:
                try:
                    result = self.sync(symbol, timeframe=timeframe, from_date=from_date, till_date=till_date)
                    items.append({"status": "PASS", **result.to_dict()})
                except Exception as exc:
                    items.append({"status": "ERROR", "symbol": symbol, "timeframe": timeframe, "error_type": type(exc).__name__, "error": str(exc)[:1000]})
        passed=sum(1 for item in items if item["status"] == "PASS")
        return {"status": "PASS" if passed == len(items) else "PARTIAL", "requested": len(items), "passed": passed, "failed": len(items)-passed, "items": items}

    def load_exact(
        self,
        instrument: Instrument,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
    ) -> CandleSeries:
        return self.store.read(instrument, timeframe, from_date, till_date)

    def _segments(
        self,
        symbol: str,
        start: date,
        finish: date,
    ) -> tuple[tuple[Instrument, date, date], ...]:
        direct = self.market_data.direct_resolver.resolve(symbol) if self.market_data.direct_resolver else None
        if direct is not None and direct.asset_class != "unknown":
            return ((direct, start, finish),)

        resolver = self.market_data.historical_future_resolver
        if resolver is None:
            instrument = self.market_data.resolve(symbol, as_of=finish)
            return ((instrument, start, finish),)

        timeline = resolver.timeline(symbol, start, finish)
        if not timeline:
            instrument = self.market_data.resolve(symbol, as_of=finish)
            return ((instrument, start, finish),)

        segments: list[tuple[Instrument, date, date]] = []
        current_instrument = timeline[0][1]
        segment_start = timeline[0][0]
        segment_end = timeline[0][0]
        for day, instrument in timeline[1:]:
            if instrument.secid == current_instrument.secid:
                segment_end = day
                continue
            segments.append((current_instrument, segment_start, segment_end))
            current_instrument = instrument
            segment_start = day
            segment_end = day
        segments.append((current_instrument, segment_start, segment_end))
        return tuple(segments)

    def _sync_contract(
        self,
        instrument: Instrument,
        timeframe: str,
        start: date,
        finish: date,
    ) -> ContractSyncResult:
        calendar = MoexTradingCalendar(self.market_data.provider)
        expected = calendar.dates(
            engine=instrument.engine,
            market=instrument.market,
            board=instrument.board,
            security=instrument.secid,
            from_date=start,
            till_date=finish,
        )
        stored_dates = self.store.stored_trade_dates(
            instrument.secid,
            timeframe,
            start.isoformat(),
            finish.isoformat(),
        )
        missing_ranges = _missing_session_ranges(expected, stored_dates)
        fetched = 0
        for left, right in missing_ranges:
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
        stored = self.store.read(
            instrument,
            timeframe,
            start.isoformat(),
            finish.isoformat(),
        )
        return ContractSyncResult(
            secid=instrument.secid,
            from_date=start.isoformat(),
            till_date=finish.isoformat(),
            fetched_ranges=tuple((left.isoformat(), right.isoformat()) for left, right in missing_ranges),
            fetched_candles=fetched,
            stored_candles=stored.count,
            coverage=coverage,
        )


def _missing_session_ranges(
    expected_sessions: tuple[date, ...],
    stored_trade_dates: tuple[str, ...],
) -> tuple[tuple[date, date], ...]:
    """Return missing runs of actual exchange sessions, never weekends by guess."""
    stored = {date.fromisoformat(item[:10]) for item in stored_trade_dates}
    ranges: list[tuple[date, date]] = []
    run_start: date | None = None
    run_end: date | None = None
    for day in expected_sessions:
        if day in stored:
            if run_start is not None and run_end is not None:
                ranges.append((run_start, run_end))
            run_start = None
            run_end = None
            continue
        if run_start is None:
            run_start = day
        run_end = day
    if run_start is not None and run_end is not None:
        ranges.append((run_start, run_end))
    return tuple(ranges)
