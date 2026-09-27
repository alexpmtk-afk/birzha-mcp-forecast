from __future__ import annotations

import json

import pytest

from birzha.domain.market import Instrument
from birzha.providers.moex_analytics import (
    AnalyticsResponse,
    MoexAnalyticsClient,
    MoexAnalyticsError,
)


BR = Instrument(
    symbol="BR",
    secid="BRV6",
    board="RFUD",
    engine="futures",
    market="forts",
    asset_class="future",
    root_symbol="BR",
)


class FakeFutoiClient(MoexAnalyticsClient):
    def __init__(self, rows_by_date: dict[str, list[list[object]]]) -> None:
        self.rows_by_date = rows_by_date
        self.calls: list[dict[str, object]] = []

    def _request(
        self,
        *,
        base: str,
        path: str,
        params: dict[str, object],
        authenticated_policy: bool,
    ) -> AnalyticsResponse:
        self.calls.append(dict(params))
        day = str(params["from"])
        payload = {
            "futoi": {
                "columns": ["tradedate", "tradetime", "ticker", "clgroup"],
                "data": self.rows_by_date.get(day, []),
            }
        }
        return AnalyticsResponse(
            status_code=200,
            headers={},
            body=json.dumps(payload).encode("utf-8"),
        )


def test_futoi_advances_by_date_without_start_pagination() -> None:
    client = FakeFutoiClient(
        {
            "2026-09-10": [
                ["2026-09-10", "10:00:00", "BR", "FIZ"],
                ["2026-09-10", "10:00:00", "BR", "YUR"],
            ],
            "2026-09-11": [
                ["2026-09-11", "10:00:00", "BR", "FIZ"],
            ],
        }
    )

    rows = client.fetch_futoi(
        BR,
        from_date="2026-09-10",
        till_date="2026-09-12",
    )

    assert len(rows) == 3
    assert [call["from"] for call in client.calls] == [
        "2026-09-10",
        "2026-09-11",
        "2026-09-12",
    ]
    assert all(call["from"] == call["till"] for call in client.calls)
    assert all("start" not in call for call in client.calls)


def test_futoi_fails_closed_when_one_day_hits_iss_row_limit() -> None:
    client = FakeFutoiClient(
        {
            "2026-09-10": [
                ["2026-09-10", f"{index:04d}", "BR", "FIZ"]
                for index in range(1000)
            ]
        }
    )

    with pytest.raises(MoexAnalyticsError, match="1000-row ISS limit"):
        client.fetch_futoi(
            BR,
            from_date="2026-09-10",
            till_date="2026-09-10",
        )
