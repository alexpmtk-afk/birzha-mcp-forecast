from __future__ import annotations

from types import SimpleNamespace

import pytest

from birzha.application.history_policy import IntradayPersistenceForbiddenError
from birzha.application.orchestration_worker import OrchestrationWorker


class FakeStore:
    def __init__(self):
        self.session_dates = set()

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

    def record_sessions(self, symbol, secid, trade_dates):
        del symbol, secid
        self.session_dates.update(trade_dates)

    def stored_sessions(self, symbol, left, right):
        del symbol
        return tuple(item for item in sorted(self.session_dates) if left <= item <= right)


class FakeHistory:
    def __init__(self):
        self.store = FakeStore()
        self.market_data = object()
        self.calls = []
        self.edge_calls = []

    def sync(self, symbol, *, timeframe, from_date, till_date):
        self.calls.append((symbol, timeframe, from_date, till_date))
        return SimpleNamespace(
            symbol=symbol,
            timeframe=timeframe,
            requested_from=from_date,
            requested_till=till_date,
            fetched_candles=3,
            stored_candles=3,
            reused_verified_range=False,
        )

    def _segments(self, symbol, start, finish):
        self.edge_calls.append((symbol, start.isoformat(), finish.isoformat()))
        return ((SimpleNamespace(secid=f"{symbol}-EDGE"), start, finish),)

    def _sync_contract(
        self,
        symbol,
        instrument,
        timeframe,
        start,
        finish,
        *,
        force_full_sessions=False,
        verification_symbol=None,
        session_symbol=None,
    ):
        del timeframe, start, force_full_sessions, verification_symbol
        self.store.record_sessions(
            session_symbol or symbol,
            instrument.secid,
            (finish.isoformat(),),
        )
        return SimpleNamespace(fetched_candles=0, stored_candles=1)


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
    instance = worker(mirror=mirror, required=True)
    result = instance._finalize_history_range(payload("D1"))
    assert result["status"] == "PASS"
    assert result["edge_revalidation"]["last_session"] == "2026-09-14"
    assert result["market_mirror"]["status"] == "MIRROR_SYNC_PASS"
    assert mirror.calls == [("BR", "2026-09-01", "2026-09-14")]


def test_d1_finalization_fails_if_mirror_does_not_pass():
    mirror = FakeMirror(status="FAILED")
    with pytest.raises(RuntimeError, match="did not pass parity"):
        worker(mirror=mirror, required=True)._finalize_history_range(payload("D1"))


def test_intraday_durable_finalization_is_forbidden():
    mirror = FakeMirror()
    with pytest.raises(IntradayPersistenceForbiddenError, match="D1-only"):
        worker(mirror=mirror, required=True)._finalize_history_range(payload("H1"))
    assert mirror.calls == []


def test_archive_refresh_requires_google_mirror_even_if_generic_flag_is_false():
    with pytest.raises(RuntimeError, match="requires Google Bridge v1 mirror"):
        worker(required=False)._sync_d1_archive(
            {
                "symbol": "BR",
                "timeframe": "D1",
                "from_date": "2021-01-01",
                "till_date": "2026-09-14",
            }
        )


def test_archive_refresh_passes_only_after_ydb_and_google_parity():
    mirror = FakeMirror()
    instance = worker(mirror=mirror, required=True)
    result = instance._sync_d1_archive(
        {
            "symbol": "BR",
            "timeframe": "D1",
            "from_date": "2021-01-01",
            "till_date": "2026-09-14",
        }
    )
    assert result["status"] == "PASS"
    assert result["market_mirror"]["status"] == "MIRROR_SYNC_PASS"
    assert instance.history.calls == [("BR", "D1", "2021-01-01", "2026-09-14")]
    assert mirror.calls == [("BR", "2021-01-01", "2026-09-14")]
