"""Runtime YDB adapters for schemas that were provisioned beforehand.

Recurring application and data-preparation processes must not issue schema DDL
on every startup. Provisioning classes remain available in their original
modules; these runtime variants reuse the same data-path behavior while
skipping CREATE TABLE operations.

Durable market-price history is D1-only. H1/M15 are on-demand analysis inputs
and runtime code must never write them into shared YDB candle history.
"""

from __future__ import annotations

from birzha.application.history_policy import require_persistent_price_timeframe
from birzha.domain.market import CandleSeries
from birzha.storage.ydb_historical_flow_store import YdbHistoricalFlowStore
from birzha.storage.ydb_historical_store import YdbHistoricalCandleStore
from birzha.storage.ydb_rate_gate import YdbSlotPacingGate


class YdbRuntimeSlotPacingGate(YdbSlotPacingGate):
    """Use an already-provisioned upstream pacing table without schema DDL."""

    def _init_schema(self) -> None:
        return None


class YdbRuntimeHistoricalCandleStore(YdbHistoricalCandleStore):
    """Use provisioned D1 history tables without DDL or intraday writes."""

    def _init_schema(self) -> None:
        return None

    def upsert_series(self, series: CandleSeries) -> int:
        require_persistent_price_timeframe(series.timeframe)
        return super().upsert_series(series)

    def mark_verified(
        self,
        symbol: str,
        timeframe: str,
        from_date: str,
        till_date: str,
    ) -> None:
        require_persistent_price_timeframe(timeframe)
        super().mark_verified(symbol, timeframe, from_date, till_date)


class YdbRuntimeHistoricalFlowStore(YdbHistoricalFlowStore):
    """Use already-provisioned historical flow tables without schema DDL."""

    def _init_schema(self) -> None:
        return None
