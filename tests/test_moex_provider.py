from datetime import date

from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.domain.market import Candle
from birzha.providers.moex_iss import IssResponse, MoexIssClient


def _json_response(payload: str) -> IssResponse:
    return IssResponse(status_code=200, headers={}, body=payload.encode())


def test_resolve_active_future_prefers_liquidity_then_oi():
    client = object.__new__(MoexIssClient)
    payload = r'''{
      "securities": {
        "columns": ["SECID","SHORTNAME","NAME","BOARDID","LASTTRADEDATE","ASSETCODE"],
        "data": [
          ["SiU6","Si-9.26","USD/RUB 9.26","RFUD","2026-09-17","Si"],
          ["SiZ6","Si-12.26","USD/RUB 12.26","RFUD","2026-12-17","Si"],
          ["BRV6","BR-10.26","Brent","RFUD","2026-10-01","BR"]
        ]
      },
      "marketdata": {
        "columns": ["SECID","LAST","VALTODAY","OPENPOSITION","NUMTRADES"],
        "data": [
          ["SiU6",80.0,5000000,150000,8000],
          ["SiZ6",81.0,1000000,300000,4000],
          ["BRV6",70.0,9000000,200000,7000]
        ]
      }
    }'''
    client._request = lambda *args, **kwargs: _json_response(payload)

    instrument = client.resolve_active_future("Si", as_of=date(2026, 8, 30))

    assert instrument.secid == "SiU6"
    assert instrument.root_symbol == "Si"
    assert instrument.asset_class == "future"
    assert instrument.last_trade_date == "2026-09-17"


def test_m15_aggregation_marks_complete_only_for_15_minutes():
    candles = tuple(
        Candle(
            open=100.0 + i,
            close=100.5 + i,
            high=101.0 + i,
            low=99.0 + i,
            value=10.0,
            volume=1.0,
            begin=f"2026-08-28T10:{i:02d}:00",
            end=f"2026-08-28T10:{i:02d}:59",
        )
        for i in range(16)
    )

    aggregated = MoexIssClient._aggregate_m15(candles)

    assert len(aggregated) == 2
    assert aggregated[0].completed is True
    assert aggregated[0].open == 100.0
    assert aggregated[0].close == 114.5
    assert aggregated[0].volume == 15.0
    assert aggregated[1].completed is False

class _NoopGate:
    scope = "process"
    def pace(self, min_interval_seconds: float) -> None:
        return None


class _FakeHttpResponse:
    status = 200
    headers: dict[str, str] = {}
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def read(self) -> bytes:
        return b"{}"


def _retry_test_client(opener):
    control = ProcessUpstreamControlPlane(
        gate_factory=lambda _: _NoopGate(), sleeper=lambda _: None,
    )
    return MoexIssClient(control_plane=control, opener=opener)


def test_single_request_retries_transient_timeout():
    calls = {"count": 0}
    def opener(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("temporary MOEX timeout")
        return _FakeHttpResponse()
    response = _retry_test_client(opener)._request("/test")
    assert response.status_code == 200
    assert calls["count"] == 2


def test_batched_request_retries_transient_connection_error():
    calls = {"count": 0}
    def opener(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("temporary MOEX reset")
        return _FakeHttpResponse()
    responses = _retry_test_client(opener)._request_many((("/test", {}),))
    assert responses[0].status_code == 200
    assert calls["count"] == 2

