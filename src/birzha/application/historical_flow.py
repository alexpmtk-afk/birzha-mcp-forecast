"""Persistent analytical history for TradeStats and FUTOI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.application.market_data import MarketDataService, is_futures_root_symbol
from birzha.domain.market import Instrument
from birzha.providers.moex_analytics import MoexAnalyticsClient
from birzha.storage.historical_flow_store import HistoricalFlowStore


FLOW_VERIFICATION_VERSION = "FLOW_V1"


def _verification_dataset(dataset: str) -> str:
    return f"{dataset}#{FLOW_VERIFICATION_VERSION}"


@dataclass(slots=True)
class HistoricalFlowDataService:
    market_data: MarketDataService
    analytics: MoexAnalyticsClient
    store: HistoricalFlowStore

    def tradestats(
        self, instrument: Instrument, *, from_date: str, till_date: str
    ) -> list[dict[str, object]]:
        dataset = "TRADESTATS"
        verification_dataset = _verification_dataset(dataset)
        key = instrument.secid
        if not self.store.is_verified(
            verification_dataset, key, from_date, till_date
        ):
            rows = self.analytics.fetch_tradestats(
                instrument, from_date=from_date, till_date=till_date
            )
            self.store.upsert_rows(dataset, key, rows, "MOEX_ALGOPACK")
            self.store.mark_verified(
                verification_dataset, key, from_date, till_date
            )
        return self.store.read_rows(dataset, key, from_date, till_date)

    def futoi(
        self, instrument: Instrument, *, from_date: str, till_date: str
    ) -> list[dict[str, object]]:
        dataset = "FUTOI"
        verification_dataset = _verification_dataset(dataset)
        key = (instrument.root_symbol or instrument.symbol).strip()
        if instrument.asset_class != "future":
            return []
        if not self.store.is_verified(
            verification_dataset, key, from_date, till_date
        ):
            rows = self.analytics.fetch_futoi(
                instrument, from_date=from_date, till_date=till_date
            )
            self.store.upsert_rows(dataset, key, rows, "MOEX_FUTOI")
            self.store.mark_verified(
                verification_dataset, key, from_date, till_date
            )
        return self.store.read_rows(dataset, key, from_date, till_date)

    def sync(self, symbol: str, *, from_date: str, till_date: str) -> dict[str, object]:
        start = date.fromisoformat(from_date[:10])
        finish = date.fromisoformat(till_date[:10])
        if start > finish:
            raise ValueError("from_date must not be after till_date")
        segments = self._segments(symbol, start, finish)
        trade_count = 0
        for instrument, left, right in segments:
            trade_count += len(
                self.tradestats(
                    instrument,
                    from_date=left.isoformat(),
                    till_date=right.isoformat(),
                )
            )
        futoi_count = 0
        future = next(
            (item[0] for item in reversed(segments) if item[0].asset_class == "future"),
            None,
        )
        if future is not None:
            futoi_count = len(
                self.futoi(
                    future,
                    from_date=start.isoformat(),
                    till_date=finish.isoformat(),
                )
            )
        return {
            "symbol": symbol,
            "from_date": start.isoformat(),
            "till_date": finish.isoformat(),
            "contract_segments": len(segments),
            "tradestats_rows": trade_count,
            "futoi_rows": futoi_count,
        }

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
