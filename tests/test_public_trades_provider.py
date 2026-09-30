from __future__ import annotations

import json
from urllib.error import URLError

from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.market import Instrument
from birzha.providers.moex_analytics import AnalyticsResponse, ISS_BASE, MoexAnalyticsClient


INSTRUMENT = Instrument(
    symbol="BR",
    secid="BRV6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="BR",
)


def test_public_recent_trades_uses_public_iss_and_start_pagination() -> None:
    client = object.__new__(MoexAnalyticsClient)
    starts: list[int] = []
    bases: list[str] = []

    def fake_request(*, base, path, params, authenticated_policy):
        bases.append(base)
        assert path.endswith("/BRV6/trades.json")
        assert authenticated_policy is False
        assert params["trades.columns"] == (
            "RECNO,TRADENO,TRADEDATE,TRADETIME,PRICE,QUANTITY,"
            "OPENPOSITION,BUYSELL,OFFMARKETDEAL"
        )
        assert params["limit"] == 500
        start = int(params["start"])
        starts.append(start)
        count = 500 if start == 0 else 2
        payload = {
            "trades": {
                "columns": ["RECNO", "TRADEDATE", "TRADETIME"],
                "data": [
                    [start + i, "2026-09-26", "10:00:00"]
                    for i in range(count)
                ],
            }
        }
        return AnalyticsResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
        )

    client._request = fake_request  # type: ignore[method-assign,attr-defined]

    rows = client.fetch_public_recent_trades(INSTRUMENT, max_pages=3)

    assert len(rows) == 502
    assert starts == [0, 500]
    assert bases == [ISS_BASE, ISS_BASE]
    assert rows[0]["RECNO"] == 0
    assert rows[-1]["RECNO"] == 501


class _FakeHttpResponse:
    status = 200
    headers: dict[str, str] = {}

    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def _two_trade_payload() -> dict[str, object]:
    return {
        "trades": {
            "columns": ["RECNO", "TRADEDATE", "TRADETIME"],
            "data": [
                [1, "2026-09-29", "10:00:00"],
                [2, "2026-09-29", "10:00:01"],
            ],
        }
    }


def _no_wait_control_plane() -> ProcessUpstreamControlPlane:
    return ProcessUpstreamControlPlane(
        clock=lambda: 0.0,
        sleeper=lambda _: None,
    )


def test_public_recent_trades_retries_direct_read_timeout() -> None:
    attempts = 0

    def opener(request, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 20.0
        if attempts == 1:
            raise TimeoutError("The read operation timed out")
        return _FakeHttpResponse(_two_trade_payload())

    client = MoexAnalyticsClient(
        control_plane=_no_wait_control_plane(),
        opener=opener,
    )

    rows = client.fetch_public_recent_trades(INSTRUMENT, max_pages=2)

    assert attempts == 2
    assert len(rows) == 2


def test_public_recent_trades_retries_urlerror_wrapped_timeout() -> None:
    attempts = 0

    def opener(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise URLError(TimeoutError("timed out"))
        return _FakeHttpResponse(_two_trade_payload())

    client = MoexAnalyticsClient(
        control_plane=_no_wait_control_plane(),
        opener=opener,
    )

    rows = client.fetch_public_recent_trades(INSTRUMENT, max_pages=2)

    assert attempts == 2
    assert len(rows) == 2


def test_public_trade_page_checkpoint_uses_actual_rows_not_page_capacity() -> None:
    client = object.__new__(MoexAnalyticsClient)

    def fake_request(*, base, path, params, authenticated_policy):
        assert int(params["start"]) == 500
        payload = {
            "trades": {
                "columns": ["RECNO", "TRADEDATE", "TRADETIME"],
                "data": [
                    [500 + i, "2026-09-30", "12:00:00"]
                    for i in range(250)
                ],
            },
            "trades.cursor": {
                "columns": ["INDEX", "TOTAL", "PAGESIZE"],
                "data": [[500, 750, 500]],
            },
        }
        return AnalyticsResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
        )

    client._request = fake_request  # type: ignore[method-assign,attr-defined]

    page, next_start, done = client.fetch_public_recent_trade_page(
        INSTRUMENT,
        start=500,
    )

    assert len(page) == 250
    assert next_start == 750
    assert done is True
