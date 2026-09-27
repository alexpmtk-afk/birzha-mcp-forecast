from __future__ import annotations

import json

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
        start = int(params["start"])
        starts.append(start)
        count = 1000 if start == 0 else 2
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

    assert len(rows) == 1002
    assert starts == [0, 1000]
    assert bases == [ISS_BASE, ISS_BASE]
    assert rows[0]["RECNO"] == 0
    assert rows[-1]["RECNO"] == 1001
