"""Historical Data Foundation: durable coverage, gap repair and rollover-safe sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.application.market_data import MarketDataService, is_futures_root_symbol
from birzha.domain.market import CandleSeries, Instrument
from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.storage.historical_store import HistoricalCandleStore, HistoricalCoverage


INTRADAY_MAX_SESSIONS_PER_FETCH = 3
D1_SESSION_VERIFICATION_VERSION = "D1_SESSION_V1"
H1_FULL_VERIFICATION_VERSION = "H1_FULL_V1"
M15_FULL_VERIFICATION_VERSION = "M15_FULL_V1"
ROLLING_HISTORY_VERIFICATION_VERSION = "ROLLING_HISTORY_V1"
FULL_SESSION_TIMEFRAMES = frozenset({"H1", "M15"})


class HistoricalDataIncompleteError(RuntimeError):
    """Expected exchange sessions are still absent after a sync attempt."""


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
        timeframe = timeframe.upper()
        start = date.fromisoformat(from_date[:10])
        finish = date.fromisoformat(till_date[:10])
        if finish < start:
            raise ValueError("till_date must be on or after from_date")

        resolver_known = hasattr(self.market_data, "direct_resolver")
        is_root = is_futures_root_symbol(symbol)
        verification_symbol = (
            _verification_symbol(symbol, timeframe, is_root=is_root)
            if resolver_known
            else symbol
        )
        session_symbol = (
            verification_symbol
            if resolver_known and timeframe == "D1"
            else symbol
        )

        # Store-first: a verification marker created by the current semantics
        # is sufficient to reuse history without any resolver/network call.
        # Validation timeframes use versioned keys, so legacy markers cannot
        # accidentally prove readiness after the M23 correctness audit.
        price_verified = self.store.is_verified(
            verification_symbol, timeframe, from_date[:10], till_date[:10]
        )
        sessions_verified = (
            timeframe != "D1"
            or self.store.is_session_range_verified(
                session_symbol, from_date[:10], till_date[:10]
            )
        )
        if price_verified and sessions_verified:
            return HistoricalSyncResult(
                symbol=symbol,
                timeframe=timeframe,
                requested_from=from_date,
                requested_till=till_date,
                contracts=(),
                reused_verified_range=True,
            )

        segments = self._segments(symbol, start, finish)
        if not segments:
            raise HistoricalDataIncompleteError(
                f"historical resolver produced no segments for {symbol} "
                f"{timeframe} {start.isoformat()}..{finish.isoformat()}"
            )

        # H1/M15 are certified from complete provider responses for each
        # expected session. Per-chunk markers make a failed long pass resumable
        # and future range extensions fetch only newly unverified sessions.
        force_full_sessions = timeframe in FULL_SESSION_TIMEFRAMES and resolver_known
        results = tuple(
            self._sync_contract(
                symbol,
                instrument,
                timeframe,
                left,
                right,
                force_full_sessions=force_full_sessions,
                verification_symbol=(
                    verification_symbol if force_full_sessions else None
                ),
                session_symbol=(session_symbol if timeframe == "D1" else None),
            )
            for instrument, left, right in segments
        )
        self.store.mark_verified(symbol, timeframe, from_date[:10], till_date[:10])
        if verification_symbol != symbol:
            self.store.mark_verified(
                verification_symbol, timeframe, from_date[:10], till_date[:10]
            )
        if timeframe == "D1":
            self.store.mark_session_range_verified(
                session_symbol, from_date[:10], till_date[:10]
            )
        return HistoricalSyncResult(
            symbol=symbol,
            timeframe=timeframe,
            requested_from=from_date,
            requested_till=till_date,
            contracts=results,
        )

    def is_range_verified(
        self,
        symbol: str,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
    ) -> bool:
        """Pure readiness check; never fetch or mutate market history."""
        timeframe = timeframe.upper()
        resolver_known = hasattr(self.market_data, "direct_resolver")
        is_root = is_futures_root_symbol(symbol)
        verification_symbol = (
            _verification_symbol(symbol, timeframe, is_root=is_root)
            if resolver_known
            else symbol
        )
        session_symbol = (
            verification_symbol
            if resolver_known and timeframe == "D1"
            else symbol
        )
        if not self.store.is_verified(
            verification_symbol, timeframe, from_date[:10], till_date[:10]
        ):
            return False
        if timeframe == "D1" and not self.store.is_session_range_verified(
            session_symbol, from_date[:10], till_date[:10]
        ):
            return False
        return True

    def sync_many(
        self, symbols: list[str], timeframes: list[str], *, from_date: str, till_date: str
    ) -> dict[str, object]:
        if not symbols or not timeframes:
            raise ValueError("symbols and timeframes must be non-empty")
        items: list[dict[str, object]] = []
        for symbol in symbols:
            for timeframe in timeframes:
                try:
                    result = self.sync(
                        symbol, timeframe=timeframe, from_date=from_date, till_date=till_date
                    )
                    items.append({"status": "PASS", **result.to_dict()})
                except Exception as exc:
                    items.append(
                        {
                            "status": "ERROR",
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:1000],
                        }
                    )
        passed = sum(1 for item in items if item["status"] == "PASS")
        return {
            "status": "PASS" if passed == len(items) else "PARTIAL",
            "requested": len(items),
            "passed": passed,
            "failed": len(items) - passed,
            "items": items,
        }

    def load_exact(
        self,
        instrument: Instrument,
        *,
        timeframe: str,
        from_date: str,
        till_date: str,
    ) -> CandleSeries:
        return self.store.read(instrument, timeframe, from_date, till_date)

    def session_dates(
        self, symbol: str, *, from_date: str, till_date: str
    ) -> tuple[date, ...]:
        resolver_known = hasattr(self.market_data, "direct_resolver")
        is_root = is_futures_root_symbol(symbol)
        session_symbol = (
            _verification_symbol(symbol, "D1", is_root=is_root)
            if resolver_known
            else symbol
        )
        if not self.store.is_session_range_verified(
            session_symbol, from_date[:10], till_date[:10]
        ):
            raise RuntimeError("stored session calendar is not verified for requested range")
        return tuple(
            date.fromisoformat(item[:10])
            for item in self.store.stored_sessions(
                session_symbol, from_date, till_date
            )
        )

    def _segments(
        self,
        symbol: str,
        start: date,
        finish: date,
    ) -> tuple[tuple[Instrument, date, date], ...]:
        direct = None
        if not is_futures_root_symbol(symbol) and self.market_data.direct_resolver:
            direct = self.market_data.direct_resolver.resolve(symbol)
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
        symbol: str,
        instrument: Instrument,
        timeframe: str,
        start: date,
        finish: date,
        *,
        force_full_sessions: bool = False,
        verification_symbol: str | None = None,
        session_symbol: str | None = None,
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
        if timeframe.upper() == "D1":
            self.store.record_sessions(
                session_symbol or symbol,
                instrument.secid,
                tuple(day.isoformat() for day in expected),
            )
        stored_dates = self.store.stored_trade_dates(
            instrument.secid,
            timeframe,
            start.isoformat(),
            finish.isoformat(),
        )
        if force_full_sessions:
            if not verification_symbol:
                raise ValueError("verification_symbol is required for full-session sync")
            source_dates = tuple(
                day.isoformat()
                for day in expected
                if self.store.is_verified(
                    verification_symbol,
                    timeframe,
                    day.isoformat(),
                    day.isoformat(),
                )
            )
        else:
            source_dates = stored_dates
        missing_ranges = _bounded_missing_ranges(expected, source_dates, timeframe)
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
            if force_full_sessions:
                assert verification_symbol is not None
                expected_chunk = tuple(day for day in expected if left <= day <= right)
                stored_chunk = self.store.stored_trade_dates(
                    instrument.secid,
                    timeframe,
                    left.isoformat(),
                    right.isoformat(),
                )
                remaining_chunk = _missing_session_ranges(expected_chunk, stored_chunk)
                if remaining_chunk:
                    compact = ",".join(
                        left_day.isoformat()
                        if left_day == right_day
                        else f"{left_day.isoformat()}..{right_day.isoformat()}"
                        for left_day, right_day in remaining_chunk[:10]
                    )
                    raise HistoricalDataIncompleteError(
                        f"full-session verification remains incomplete for "
                        f"{instrument.secid} {timeframe}: {compact}"
                    )
                self.store.mark_verified(
                    verification_symbol,
                    timeframe,
                    left.isoformat(),
                    right.isoformat(),
                )

        stored_dates_after = self.store.stored_trade_dates(
            instrument.secid, timeframe, start.isoformat(), finish.isoformat()
        )
        remaining = _missing_session_ranges(expected, stored_dates_after)
        if remaining:
            compact = ",".join(
                left.isoformat()
                if left == right
                else f"{left.isoformat()}..{right.isoformat()}"
                for left, right in remaining[:10]
            )
            raise HistoricalDataIncompleteError(
                f"history remains incomplete for {instrument.secid} {timeframe}: {compact}"
            )

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
            fetched_ranges=tuple(
                (left.isoformat(), right.isoformat()) for left, right in missing_ranges
            ),
            fetched_candles=fetched,
            stored_candles=stored.count,
            coverage=coverage,
        )


def _verification_symbol(symbol: str, timeframe: str, *, is_root: bool) -> str:
    tf = timeframe.upper()
    versions: list[str] = []
    if is_root:
        versions.append(ROLLING_HISTORY_VERIFICATION_VERSION)
    if tf == "D1":
        versions.append(D1_SESSION_VERIFICATION_VERSION)
    elif tf == "H1":
        versions.append(H1_FULL_VERIFICATION_VERSION)
    elif tf == "M15":
        versions.append(M15_FULL_VERIFICATION_VERSION)
    if not versions:
        return symbol
    return f"{symbol}#" + "#".join(versions)


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


def _bounded_missing_ranges(
    expected_sessions: tuple[date, ...],
    stored_trade_dates: tuple[str, ...],
    timeframe: str,
) -> tuple[tuple[date, date], ...]:
    ranges = _missing_session_ranges(expected_sessions, stored_trade_dates)
    if timeframe.upper() not in FULL_SESSION_TIMEFRAMES:
        return ranges
    bounded: list[tuple[date, date]] = []
    for left, right in ranges:
        missing_days = [day for day in expected_sessions if left <= day <= right]
        for index in range(0, len(missing_days), INTRADAY_MAX_SESSIONS_PER_FETCH):
            chunk = missing_days[index : index + INTRADAY_MAX_SESSIONS_PER_FETCH]
            if chunk:
                bounded.append((chunk[0], chunk[-1]))
    return tuple(bounded)
