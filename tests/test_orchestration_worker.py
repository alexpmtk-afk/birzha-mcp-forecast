from __future__ import annotations

from types import SimpleNamespace

import pytest

from birzha.application.orchestration_worker import (
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

    def is_verified(self, symbol: str, timeframe: str, left: str, right: str) -> bool:
        return (symbol, timeframe, left, right) in self.verified

    def mark_verified(self, symbol: str, timeframe: str, left: str, right: str) -> None:
        self.verified.add((symbol, timeframe, left, right))

    def is_session_range_verified(self, symbol: str, left: str, right: str) -> bool:
        return (symbol, left, right) in self.sessions

    def mark_session_range_verified(self, symbol: str, left: str, right: str) -> None:
        self.sessions.add((symbol, left, right))


class FakeHistory:
    def __init__(self) -> None:
        self.store = FakeHistoricalStore()
        self.market_data = SimpleNamespace(direct_resolver=object())
        self.calls: list[tuple[str, str, str, str]] = []

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


def _worker() -> tuple[OrchestrationWorker, FakeHistory]:
    history = FakeHistory()
    orchestrator = WorkflowOrchestrator(MemoryOrchestrationStore())
    return OrchestrationWorker(orchestrator=orchestrator, history=history), history  # type: ignore[arg-type]


def test_default_worker_lease_is_bounded_for_serverless_recovery() -> None:
    worker, _ = _worker()
    assert DEFAULT_WORKER_LEASE_SECONDS == 180
    assert worker.lease_seconds == DEFAULT_WORKER_LEASE_SECONDS


def test_history_chunk_handler_is_bounded_and_idempotent_at_service_boundary() -> None:
    worker, history = _worker()
    action = WorkflowAction(
        action_id="a1",
        workflow_id="w1",
        stage=WorkflowStage.HISTORY_PREPARATION,
        kind="HISTORY_SYNC_CHUNK",
        sequence=1,
        payload={
            "symbol": "SBER",
            "timeframe": "M15",
            "from_date": "2021-01-01",
            "till_date": "2021-04-02",
        },
    )
    evidence = worker._execute(action)
    assert evidence["status"] == "PASS"
    assert evidence["fetched_candles"] == 12
    assert history.calls == [("SBER", "M15", "2021-01-01", "2021-04-02")]


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


def test_finalizer_marks_full_range_only_after_all_chunks_are_verified() -> None:
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
    assert evidence["status"] == "PASS"
    assert evidence["verified_chunks"] == 2
    assert (verification_symbol, "D1", "2021-01-01", "2021-02-28") in history.store.verified
    assert (verification_symbol, "2021-01-01", "2021-02-28") in history.store.sessions
