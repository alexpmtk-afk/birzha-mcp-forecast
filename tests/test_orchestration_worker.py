from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from birzha.application.history_policy import IntradayPersistenceForbiddenError
from birzha.application.orchestration_worker import (
    D1_EDGE_REVALIDATION_DAYS,
    DEFAULT_WORKER_LEASE_SECONDS,
    OrchestrationWorker,
)
from birzha.application.orchestrator import WorkflowOrchestrator
from birzha.domain.orchestration import WorkflowAction, WorkflowStage
from birzha.storage.orchestration_store import MemoryOrchestrationStore


class FakeHistoricalStore:
    def __init__(self) -> None:
        self.verified: set[tuple[str, str, str, str]] = set()
        self.sessions: set[tuple[str, str, str]] = set()
        self.session_dates: dict[str, set[str]] = {}

    def is_verified(self, symbol: str, timeframe: str, left: str, right: str) -> bool:
        return (symbol, timeframe, left, right) in self.verified

    def mark_verified(self, symbol: str, timeframe: str, left: str, right: str) -> None:
        self.verified.add((symbol, timeframe, left, right))

    def is_session_range_verified(self, symbol: str, left: str, right: str) -> bool:
        return (symbol, left, right) in self.sessions

    def mark_session_range_verified(self, symbol: str, left: str, right: str) -> None:
        self.sessions.add((symbol, left, right))

    def record_sessions(self, symbol: str, secid: str, trade_dates: tuple[str, ...]) -> None:
        del secid
        self.session_dates.setdefault(symbol, set()).update(trade_dates)

    def stored_sessions(self, symbol: str, left: str, right: str) -> tuple[str, ...]:
        return tuple(
            item
            for item in sorted(self.session_dates.get(symbol, set()))
            if left <= item <= right
        )


class FakeHistory:
    def __init__(self) -> None:
        self.store = FakeHistoricalStore()
        self.market_data = SimpleNamespace(direct_resolver=object())
        self.calls: list[tuple[str, str, str, str]] = []
        self.edge_calls: list[tuple[str, str, str]] = []

    def sync(self, symbol: str, *, timeframe: str, from_date: str, till_date: str):
        self.calls.append((symbol, timeframe, from_date, till_date))
        return SimpleNamespace(
            symbol=symbol,
            timeframe=timeframe,
            requested_from=from_date,
            requested_till=till_date,
            fetched_candles=12,
            stored_candles=12,
            reused_verified_range=False,
        )

    def _segments(self, symbol: str, start: date, finish: date):
        self.edge_calls.append((symbol, start.isoformat(), finish.isoformat()))
        instrument = SimpleNamespace(secid=f"{symbol}-EDGE")
        return ((instrument, start, finish),)

    def _sync_contract(
        self,
        symbol: str,
        instrument,
        timeframe: str,
        start: date,
        finish: date,
        *,
        force_full_sessions: bool = False,
        verification_symbol: str | None = None,
        session_symbol: str | None = None,
    ):
        del timeframe, force_full_sessions, verification_symbol
        target = session_symbol or symbol
        self.store.record_sessions(
            target,
            str(instrument.secid),
            (finish.isoformat(),),
        )
        return SimpleNamespace(fetched_candles=1, stored_candles=1)


def _worker() -> tuple[OrchestrationWorker, FakeHistory]:
    history = FakeHistory()
    orchestrator = WorkflowOrchestrator(MemoryOrchestrationStore())
    return OrchestrationWorker(orchestrator=orchestrator, history=history), history  # type: ignore[arg-type]


def test_default_worker_lease_is_longer_than_production_mirror_timeout() -> None:
    worker, _ = _worker()
    assert DEFAULT_WORKER_LEASE_SECONDS == 420
    assert worker.lease_seconds == DEFAULT_WORKER_LEASE_SECONDS


def test_d1_history_chunk_handler_is_bounded_and_idempotent_at_service_boundary() -> None:
    worker, history = _worker()
    action = WorkflowAction(
        action_id="a1",
        workflow_id="w1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        kind="HISTORY_SYNC_CHUNK",
        sequence=1,
        payload={
            "symbol": "SBER",
            "timeframe": "D1",
            "from_date": "2021-01-01",
            "till_date": "2021-04-02",
        },
    )
    evidence = worker._execute(action)
    assert evidence["status"] == "PASS"
    assert evidence["fetched_candles"] == 12
    assert history.calls == [("SBER", "D1", "2021-01-01", "2021-04-02")]


@pytest.mark.parametrize("timeframe", ["H1", "M15"])
def test_durable_history_chunk_rejects_intraday(timeframe: str) -> None:
    worker, history = _worker()
    action = WorkflowAction(
        action_id="a1",
        workflow_id="w1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        kind="HISTORY_SYNC_CHUNK",
        sequence=1,
        payload={
            "symbol": "SBER",
            "timeframe": timeframe,
            "from_date": "2021-01-01",
            "till_date": "2021-04-02",
        },
    )
    with pytest.raises(IntradayPersistenceForbiddenError, match="D1-only"):
        worker._execute(action)
    assert history.calls == []


def test_finalizer_requires_every_chunk_before_marking_full_range() -> None:
    worker, history = _worker()
    verification_symbol = "SBER#D1_SESSION_V2_ACTIVITY"
    history.store.mark_verified(verification_symbol, "D1", "2021-01-01", "2021-01-31")
    history.store.mark_session_range_verified(verification_symbol, "2021-01-01", "2021-01-31")

    payload = {
        "symbol": "SBER",
        "timeframe": "D1",
        "from_date": "2021-01-01",
        "till_date": "2021-02-28",
        "chunks": [
            ["2021-01-01", "2021-01-31"],
            ["2021-02-01", "2021-02-28"],
        ],
    }
    with pytest.raises(RuntimeError, match="chunk verification missing"):
        worker._finalize_history_range(payload)

    assert (verification_symbol, "D1", "2021-01-01", "2021-02-28") not in history.store.verified


def test_finalizer_freshly_revalidates_right_edge_before_full_marker() -> None:
    worker, history = _worker()
    verification_symbol = "SBER#D1_SESSION_V2_ACTIVITY"
    chunks = [
        ["2021-01-01", "2021-01-31"],
        ["2021-02-01", "2021-02-28"],
    ]
    for left, right in chunks:
        history.store.mark_verified(verification_symbol, "D1", left, right)
        history.store.mark_session_range_verified(verification_symbol, left, right)

    evidence = worker._finalize_history_range(
        {
            "symbol": "SBER",
            "timeframe": "D1",
            "from_date": "2021-01-01",
            "till_date": "2021-02-28",
            "chunks": chunks,
        }
    )

    assert D1_EDGE_REVALIDATION_DAYS == 7
    assert history.edge_calls == [("SBER", "2021-02-22", "2021-02-28")]
    assert evidence["edge_revalidation"] == {
        "status": "PASS",
        "from_date": "2021-02-22",
        "till_date": "2021-02-28",
        "fresh_segments": 1,
        "fetched_candles": 1,
        "stored_candles": 1,
        "last_session": "2021-02-28",
    }
    assert (verification_symbol, "D1", "2021-01-01", "2021-02-28") in history.store.verified
    assert (verification_symbol, "2021-01-01", "2021-02-28") in history.store.sessions


def test_finalizer_does_not_mark_full_range_when_fresh_edge_has_no_sessions() -> None:
    worker, history = _worker()
    verification_symbol = "SBER#D1_SESSION_V2_ACTIVITY"
    chunks = [
        ["2021-02-01", "2021-02-14"],
        ["2021-02-15", "2021-02-28"],
    ]
    for left, right in chunks:
        history.store.mark_verified(verification_symbol, "D1", left, right)
        history.store.mark_session_range_verified(verification_symbol, left, right)

    def no_session_sync(*args, **kwargs):
        del args, kwargs
        return SimpleNamespace(fetched_candles=0, stored_candles=0)

    history._sync_contract = no_session_sync  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="edge revalidation found no verified sessions"):
        worker._finalize_history_range(
            {
                "symbol": "SBER",
                "timeframe": "D1",
                "from_date": "2021-02-01",
                "till_date": "2021-02-28",
                "chunks": chunks,
            }
        )

    assert (verification_symbol, "D1", "2021-02-01", "2021-02-28") not in history.store.verified
