from __future__ import annotations

from types import SimpleNamespace

import pytest

from birzha.application.orchestration_worker import OrchestrationWorker


class FakeStore:
    def is_verified(self, symbol, timeframe, left, right):
        del symbol, timeframe, left, right
        return True

    def is_session_range_verified(self, symbol, left, right):
        del symbol, left, right
        return True

    def mark_verified(self, symbol, timeframe, left, right):
        del symbol, timeframe, left, right

    def mark_session_range_verified(self, symbol, left, right):
        del symbol, left, right


class FakeHistory:
    def __init__(self):
        self.store = FakeStore()
        self.market_data = object()


class FakeMirror:
    def __init__(self, status="MIRROR_SYNC_PASS"):
        self.status = status
        self.calls = []

    def sync(self, *, symbol, from_date, till_date):
        self.calls.append((symbol, from_date, till_date))
        return {"status": self.status, "bridge_release": "1.0.0"}


def worker(*, mirror=None, required=False):
    return OrchestrationWorker(
        orchestrator=SimpleNamespace(),
        history=FakeHistory(),
        mirror=mirror,
        require_market_mirror=required,
    )


def payload(timeframe="D1"):
    return {
        "symbol": "BR",
        "timeframe": timeframe,
        "from_date": "2026-09-01",
        "till_date": "2026-09-14",
        "chunks": [["2026-09-01", "2026-09-14"]],
    }


def test_d1_finalization_requires_configured_mirror_when_mandatory():
    with pytest.raises(RuntimeError, match="mandatory Google market mirror is not configured"):
        worker(required=True)._finalize_history_range(payload("D1"))


def test_d1_finalization_waits_for_mirror_pass():
    mirror = FakeMirror()
    result = worker(mirror=mirror, required=True)._finalize_history_range(payload("D1"))
    assert result["status"] == "PASS"
    assert result["market_mirror"]["status"] == "MIRROR_SYNC_PASS"
    assert mirror.calls == [("BR", "2026-09-01", "2026-09-14")]


def test_d1_finalization_fails_if_mirror_does_not_pass():
    mirror = FakeMirror(status="FAILED")
    with pytest.raises(RuntimeError, match="did not pass parity"):
        worker(mirror=mirror, required=True)._finalize_history_range(payload("D1"))


def test_intraday_finalization_does_not_call_google_mirror():
    mirror = FakeMirror()
    result = worker(mirror=mirror, required=True)._finalize_history_range(payload("H1"))
    assert result["status"] == "PASS"
    assert "market_mirror" not in result
    assert mirror.calls == []
