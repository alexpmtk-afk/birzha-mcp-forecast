"""Persistent analytical history for TradeStats and FUTOI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from birzha.application.market_data import MarketDataService, is_futures_root_symbol
from birzha.application.public_tradestats import (
    PUBLIC_TRADESTATS_SOURCE,
    aggregate_public_futures_trades,
)
from birzha.domain.market import Instrument
from birzha.providers.moex_analytics import MoexAnalyticsClient
from birzha.storage.historical_flow_store import HistoricalFlowStore


TRADESTATS_ASSET_CLASSES = frozenset({"future", "equity", "fx"})
FLOW_MAX_CALENDAR_DAYS_PER_FETCH = 60
FLOW_VERIFICATION_VERSION = "FLOW_V1"
PUBLIC_TRADES_RAW_DATASET = "PUBLIC_TRADES_RAW"
PUBLIC_TRADES_CHECKPOINT_DATASET = "PUBLIC_TRADES_CHECKPOINT"
PUBLIC_TRADES_RAW_SOURCE = "MOEX_ISS_PUBLIC_TRADES_RAW"
PUBLIC_TRADES_PAGES_PER_RUN = 2


def _verification_dataset(dataset: str) -> str:
    return f"{dataset}#{FLOW_VERIFICATION_VERSION}"


@dataclass(slots=True)
class HistoricalFlowDataService:
    market_data: MarketDataService
    analytics: MoexAnalyticsClient
    store: HistoricalFlowStore
    read_only: bool = False

    def tradestats(
        self, instrument: Instrument, *, from_date: str, till_date: str
    ) -> list[dict[str, object]]:
        # ALGOPACK TradeStats is optional context. Unsupported asset classes
        # (notably broad indices) degrade to an empty feed rather than blocking
        # the mandatory price path.
        if instrument.asset_class not in TRADESTATS_ASSET_CLASSES:
            return []
        dataset = "TRADESTATS"
        verification_dataset = _verification_dataset(dataset)
        key = instrument.secid
        verified = self.store.is_verified(
            verification_dataset, key, from_date, till_date
        )

        # Without subscriber authorization MOEX no longer exposes historical
        # TradeStats through public ISS. Never keep retrying that restricted
        # endpoint and never mark an unprovable historical range as verified.
        # Return only rows that BIRZHA has actually persisted from public raw
        # trades captures.
        if not bool(getattr(self.analytics, "authenticated", True)):
            return self.store.read_rows(dataset, key, from_date, till_date)

        if self.read_only:
            return (
                self.store.read_rows(dataset, key, from_date, till_date)
                if verified
                else []
            )
        if not verified:
            rows = self.analytics.fetch_tradestats(
                instrument, from_date=from_date, till_date=till_date
            )
            self.store.upsert_rows(dataset, key, rows, "MOEX_ALGOPACK")
            self.store.mark_verified(
                verification_dataset, key, from_date, till_date
            )
        return self.store.read_rows(dataset, key, from_date, till_date)

    def capture_public_recent_tradestats(self, symbol: str) -> dict[str, object]:
        """Capture public futures trades with durable page-by-page resume.

        Every successful ISS page is persisted before the next request and the
        next start offset is checkpointed in the same durable store. If a later
        page times out, the next invocation resumes from that checkpoint rather
        than downloading the trading day again from zero.
        """

        if self.read_only:
            raise RuntimeError("read-only historical flow service cannot capture public trades")
        instrument = self.market_data.resolve(symbol)
        if instrument.asset_class != "future":
            raise ValueError("public raw-trade capture is currently supported for futures only")

        capture_date = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        resume_start = self._public_trade_checkpoint(instrument.secid, capture_date)
        start = resume_start
        pages_fetched = 0
        raw_trades_fetched = 0

        complete = False
        for _ in range(PUBLIC_TRADES_PAGES_PER_RUN):
            page, next_start, done = self.analytics.fetch_public_recent_trade_page(
                instrument,
                start=start,
            )
            if page:
                self.store.upsert_rows(
                    PUBLIC_TRADES_RAW_DATASET,
                    instrument.secid,
                    page,
                    PUBLIC_TRADES_RAW_SOURCE,
                )
                raw_trades_fetched += len(page)
                pages_fetched += 1

            if next_start < start:
                raise RuntimeError(
                    f"public trades checkpoint moved backwards: {start} -> {next_start}"
                )
            if next_start > start:
                self._save_public_trade_checkpoint(
                    instrument.secid,
                    capture_date,
                    next_start,
                )
                start = next_start

            if done:
                complete = True
                break
            if not page and next_start == start:
                raise RuntimeError(
                    f"public trades pagination stalled at start={start}"
                )

        raw_rows = self.store.read_rows(
            PUBLIC_TRADES_RAW_DATASET,
            instrument.secid,
            capture_date,
            capture_date,
        )
        derived: list[dict[str, object]] = []
        written = 0
        if complete:
            derived = aggregate_public_futures_trades(raw_rows)
            written = self.store.upsert_rows(
                "TRADESTATS",
                instrument.secid,
                derived,
                PUBLIC_TRADESTATS_SOURCE,
            )
        trade_dates = sorted(
            {
                str(row.get("tradedate") or "")[:10]
                for row in derived
                if row.get("tradedate")
            }
        )
        return {
            "symbol": symbol,
            "secid": instrument.secid,
            "source": PUBLIC_TRADESTATS_SOURCE,
            "capture_date": capture_date,
            "complete": complete,
            "resume_start": resume_start,
            "next_start": start,
            "pages_fetched": pages_fetched,
            "raw_trades_fetched": raw_trades_fetched,
            "raw_trades_total": len(raw_rows),
            "derived_5m_rows": len(derived),
            "rows_written": written,
            "trade_dates": trade_dates,
            "value_fields": "UNAVAILABLE_IN_PUBLIC_FUTURES_TRADES",
            "historical_backfill": "NOT_AVAILABLE_FROM_PUBLIC_RAW_TRADES",
            "historical_range_marked_verified": False,
        }

    def _public_trade_checkpoint(self, secid: str, capture_date: str) -> int:
        rows = self.store.read_rows(
            PUBLIC_TRADES_CHECKPOINT_DATASET,
            secid,
            capture_date,
            capture_date,
        )
        values = []
        for row in rows:
            try:
                values.append(int(row.get("next_start") or 0))
            except (TypeError, ValueError):
                continue
        return max(values, default=0)

    def _save_public_trade_checkpoint(
        self,
        secid: str,
        capture_date: str,
        next_start: int,
    ) -> None:
        self.store.upsert_rows(
            PUBLIC_TRADES_CHECKPOINT_DATASET,
            secid,
            [
                {
                    "tradedate": capture_date,
                    "tradetime": "00:00:00",
                    "next_start": int(next_start),
                }
            ],
            PUBLIC_TRADES_RAW_SOURCE,
        )

    def futoi(
        self, instrument: Instrument, *, from_date: str, till_date: str
    ) -> list[dict[str, object]]:
        dataset = "FUTOI"
        verification_dataset = _verification_dataset(dataset)
        key = (instrument.root_symbol or instrument.symbol).strip()
        if instrument.asset_class != "future":
            return []
        verified = self.store.is_verified(
            verification_dataset, key, from_date, till_date
        )
        if self.read_only:
            return (
                self.store.read_rows(dataset, key, from_date, till_date)
                if verified
                else []
            )
        if not verified:
            rows = self.analytics.fetch_futoi(
                instrument, from_date=from_date, till_date=till_date
            )
            self.store.upsert_rows(dataset, key, rows, "MOEX_FUTOI")
            self.store.mark_verified(
                verification_dataset, key, from_date, till_date
            )
        return self.store.read_rows(dataset, key, from_date, till_date)

    def sync(self, symbol: str, *, from_date: str, till_date: str) -> dict[str, object]:
        if self.read_only:
            raise RuntimeError("read-only historical flow service cannot sync")
        start = date.fromisoformat(from_date[:10])
        finish = date.fromisoformat(till_date[:10])
        if start > finish:
            raise ValueError("from_date must not be after till_date")
        segments = self._segments(symbol, start, finish)
        trade_count = 0
        for instrument, left, right in segments:
            trade_count += len(self._tradestats_bounded(instrument, left, right))
        futoi_count = 0
        future = next(
            (item[0] for item in reversed(segments) if item[0].asset_class == "future"),
            None,
        )
        if future is not None:
            futoi_count = len(self._futoi_bounded(future, start, finish))
        return {
            "symbol": symbol,
            "from_date": start.isoformat(),
            "till_date": finish.isoformat(),
            "contract_segments": len(segments),
            "tradestats_rows": trade_count,
            "futoi_rows": futoi_count,
        }

    def _tradestats_bounded(
        self, instrument: Instrument, start: date, finish: date
    ) -> list[dict[str, object]]:
        if instrument.asset_class not in TRADESTATS_ASSET_CLASSES:
            return []
        dataset = "TRADESTATS"
        verification_dataset = _verification_dataset(dataset)
        key = instrument.secid

        if not bool(getattr(self.analytics, "authenticated", True)):
            return self.store.read_rows(
                dataset, key, start.isoformat(), finish.isoformat()
            )

        if self.store.is_verified(
            verification_dataset, key, start.isoformat(), finish.isoformat()
        ):
            return self.store.read_rows(dataset, key, start.isoformat(), finish.isoformat())
        for left, right in _bounded_date_ranges(start, finish):
            self.tradestats(
                instrument, from_date=left.isoformat(), till_date=right.isoformat()
            )
        self.store.mark_verified(
            verification_dataset, key, start.isoformat(), finish.isoformat()
        )
        return self.store.read_rows(dataset, key, start.isoformat(), finish.isoformat())

    def _futoi_bounded(
        self, instrument: Instrument, start: date, finish: date
    ) -> list[dict[str, object]]:
        dataset = "FUTOI"
        verification_dataset = _verification_dataset(dataset)
        key = (instrument.root_symbol or instrument.symbol).strip()
        if self.store.is_verified(
            verification_dataset, key, start.isoformat(), finish.isoformat()
        ):
            return self.store.read_rows(dataset, key, start.isoformat(), finish.isoformat())
        for left, right in _bounded_date_ranges(start, finish):
            self.futoi(instrument, from_date=left.isoformat(), till_date=right.isoformat())
        self.store.mark_verified(
            verification_dataset, key, start.isoformat(), finish.isoformat()
        )
        return self.store.read_rows(dataset, key, start.isoformat(), finish.isoformat())

    def _segments(self, symbol: str, start: date, finish: date):
        direct = None
        if not is_futures_root_symbol(symbol) and self.market_data.direct_resolver:
            direct = self.market_data.direct_resolver.resolve(symbol)
        if direct is not None and direct.asset_class != "unknown":
            return ((direct, start, finish),)
        resolver = self.market_data.historical_future_resolver
        if resolver is None:
            return ((self.market_data.resolve(symbol, as_of=finish), start, finish),)
        timeline = resolver.timeline(symbol, start, finish)
        if not timeline:
            return ((self.market_data.resolve(symbol, as_of=finish), start, finish),)
        result = []
        current = timeline[0][1]
        left = timeline[0][0]
        right = left
        for day, instrument in timeline[1:]:
            if instrument.secid == current.secid:
                right = day
                continue
            result.append((current, left, right))
            current = instrument
            left = day
            right = day
        result.append((current, left, right))
        return tuple(result)


def _bounded_date_ranges(start: date, finish: date) -> tuple[tuple[date, date], ...]:
    ranges: list[tuple[date, date]] = []
    cursor = start
    while cursor <= finish:
        right = min(
            finish,
            cursor + timedelta(days=FLOW_MAX_CALENDAR_DAYS_PER_FETCH - 1),
        )
        ranges.append((cursor, right))
        cursor = right + timedelta(days=1)
    return tuple(ranges)
