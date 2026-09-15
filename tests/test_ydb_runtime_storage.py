from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from birzha.application.history_policy import IntradayPersistenceForbiddenError
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.storage.ydb_runtime_storage import (
    YdbRuntimeHistoricalCandleStore,
    YdbRuntimeHistoricalFlowStore,
    YdbRuntimeSlotPacingGate,
)


@dataclass
class FakePool:
    calls: list[str] = field(default_factory=list)

    def execute_with_retries(self, query, parameters=None, **kwargs):
        self.calls.append(str(query))
        return []


def _series(timeframe: str) -> CandleSeries:
    instrument = Instrument(
        symbol="SBER",
        secid="SBER",
        board="TQBR",
        engine="stock",
        market="shares",
        asset_class="equity",
    )
    candle = Candle(
        open=100,
        close=101,
        high=102,
        low=99,
        value=1000,
        volume=10,
        begin="2026-09-01T00:00:00",
        end="2026-09-01T23:59:59",
        completed=True,
    )
    return CandleSeries(instrument, timeframe, (candle,))


def test_runtime_adapters_do_not_issue_schema_ddl_on_construction() -> None:
    pool = FakePool()

    YdbRuntimeSlotPacingGate(pool, provider_key="moex-public")
    YdbRuntimeHistoricalCandleStore(pool)
    YdbRuntimeHistoricalFlowStore(pool)

    assert pool.calls == []


def test_runtime_rate_gate_still_uses_data_path_after_no_ddl_construction() -> None:
    pool = FakePool()
    gate = YdbRuntimeSlotPacingGate(
        pool,
        provider_key="moex-public",
        clock=lambda: 1.0,
        sleeper=lambda _seconds: None,
        safety_margin_seconds=0.0,
    )

    gate.pace(1.0)

    assert len(pool.calls) == 1
    assert "INSERT INTO `upstream_rate_slots`" in pool.calls[0]
    assert "CREATE TABLE" not in pool.calls[0]


@pytest.mark.parametrize("timeframe", ["H1", "M15"])
def test_runtime_candle_store_rejects_intraday_series_without_query(timeframe: str) -> None:
    pool = FakePool()
    store = YdbRuntimeHistoricalCandleStore(pool)

    with pytest.raises(IntradayPersistenceForbiddenError, match="D1-only"):
        store.upsert_series(_series(timeframe))

    assert pool.calls == []


@pytest.mark.parametrize("timeframe", ["H1", "M15"])
def test_runtime_candle_store_rejects_intraday_verification_markers(timeframe: str) -> None:
    pool = FakePool()
    store = YdbRuntimeHistoricalCandleStore(pool)

    with pytest.raises(IntradayPersistenceForbiddenError, match="D1-only"):
        store.mark_verified("SBER", timeframe, "2026-09-01", "2026-09-02")

    assert pool.calls == []
