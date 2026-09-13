"""Runtime YDB adapters for schemas that were provisioned beforehand.

Recurring application and data-preparation processes must not issue schema DDL
on every startup.  Provisioning classes remain available in their original
modules; these runtime variants reuse the same data-path behavior while
skipping CREATE TABLE operations.
"""

from __future__ import annotations

from birzha.storage.ydb_historical_flow_store import YdbHistoricalFlowStore
from birzha.storage.ydb_historical_store import YdbHistoricalCandleStore
from birzha.storage.ydb_rate_gate import YdbSlotPacingGate


class YdbRuntimeSlotPacingGate(YdbSlotPacingGate):
    """Use an already-provisioned upstream pacing table without schema DDL."""

    def _init_schema(self) -> None:
        return None


class YdbRuntimeHistoricalCandleStore(YdbHistoricalCandleStore):
    """Use already-provisioned historical candle tables without schema DDL."""

    def _init_schema(self) -> None:
        return None


class YdbRuntimeHistoricalFlowStore(YdbHistoricalFlowStore):
    """Use already-provisioned historical flow tables without schema DDL."""

    def _init_schema(self) -> None:
        return None
