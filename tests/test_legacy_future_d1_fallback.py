import json

from birzha.domain.market import Instrument
from birzha.providers.moex_iss import IssResponse, MoexIssClient


def _response(payload: dict) -> IssResponse:
    return IssResponse(status_code=200, headers={}, body=json.dumps(payload).encode())


def _historical_future() -> Instrument:
    return Instrument(
        symbol="BR",
        secid="BRF3_2013",
        board="RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        root_symbol="BR",
        source="MOEX_ISS_HISTORY",
    )


def _history_payload(row=None) -> dict:
    columns = [
        "TRADEDATE",
        "SECID",
        "NUMTRADES",
        "VOLUME",
        "VALUE",
        "OPEN",
        "CLOSE",
        "HIGH",
        "LOW",
    ]
    return {"history": {"columns": columns, "data": [] if row is None else [row]}}


def _empty_candles() -> dict:
    return {
        "candles": {
            "columns": ["open", "close", "high", "low", "value", "volume", "begin", "end"],
            "data": [],
        }
    }


def test_historical_future_d1_recovers_real_trade_when_candle_endpoint_is_empty() -> None:
    client = object.__new__(MoexIssClient)
    row = ["2012-10-26", "BRF3_2013", 1, 10, 333318.6, 105.92, 105.92, 105.92, 105.92]

    def request(path, params):
        if path.endswith("/candles.json"):
            return _response(_empty_candles())
        if int(params.get("start", 0)) == 0:
            return _response(_history_payload(row))
        return _response(_history_payload())

    client._request = request
    series = client.fetch_candles(
        _historical_future(),
        timeframe="D1",
        from_date="2012-10-26",
        till_date="2012-10-26",
    )

    assert series.count == 1
    assert series.source == "MOEX_ISS_CANDLES+HISTORY_D1_FALLBACK"
    candle = series.candles[0]
    assert candle.open == 105.92
    assert candle.close == 105.92
    assert candle.volume == 10.0
    assert candle.begin[:10] == "2012-10-26"
    assert candle.completed is True


def test_historical_future_d1_does_not_synthesize_incomplete_history_price() -> None:
    client = object.__new__(MoexIssClient)
    row = ["2012-10-26", "BRF3_2013", 1, 10, 333318.6, 105.92, 105.92, None, 105.92]

    def request(path, params):
        if path.endswith("/candles.json"):
            return _response(_empty_candles())
        if int(params.get("start", 0)) == 0:
            return _response(_history_payload(row))
        return _response(_history_payload())

    client._request = request
    series = client.fetch_candles(
        _historical_future(),
        timeframe="D1",
        from_date="2012-10-26",
        till_date="2012-10-26",
    )

    assert series.count == 0
    assert series.source == "MOEX_ISS"


def test_historical_future_d1_does_not_synthesize_zero_activity_day() -> None:
    client = object.__new__(MoexIssClient)
    row = ["2012-10-26", "BRF3_2013", 0, 0, 0, 105.92, 105.92, 105.92, 105.92]

    def request(path, params):
        if path.endswith("/candles.json"):
            return _response(_empty_candles())
        if int(params.get("start", 0)) == 0:
            return _response(_history_payload(row))
        return _response(_history_payload())

    client._request = request
    series = client.fetch_candles(
        _historical_future(),
        timeframe="D1",
        from_date="2012-10-26",
        till_date="2012-10-26",
    )

    assert series.count == 0


def test_native_d1_candle_remains_canonical_when_history_also_has_row() -> None:
    client = object.__new__(MoexIssClient)
    native = {
        "candles": {
            "columns": ["open", "close", "high", "low", "value", "volume", "begin", "end"],
            "data": [[106.0, 107.0, 108.0, 105.0, 500000.0, 20.0, "2012-11-14T00:00:00+03:00", "2012-11-14T23:59:59+03:00"]],
        }
    }
    history_row = ["2012-11-14", "BRF3_2013", 10, 20, 674813.03, 1.0, 2.0, 3.0, 0.5]

    def request(path, params):
        start = int(params.get("start", 0))
        if path.endswith("/candles.json"):
            return _response(native if start == 0 else _empty_candles())
        return _response(_history_payload(history_row if start == 0 else None))

    client._request = request
    series = client.fetch_candles(
        _historical_future(),
        timeframe="D1",
        from_date="2012-11-14",
        till_date="2012-11-14",
    )

    assert series.count == 1
    assert series.source == "MOEX_ISS"
    assert series.candles[0].open == 106.0
    assert series.candles[0].close == 107.0


def test_non_d1_never_uses_history_fallback() -> None:
    client = object.__new__(MoexIssClient)
    paths: list[str] = []

    def request(path, params):
        paths.append(path)
        return _response(_empty_candles())

    client._request = request
    series = client.fetch_candles(
        _historical_future(),
        timeframe="H1",
        from_date="2012-10-26",
        till_date="2012-10-26",
    )

    assert series.count == 0
    assert not any(path.startswith("/history/") for path in paths)


def test_current_future_d1_never_uses_legacy_history_fallback() -> None:
    client = object.__new__(MoexIssClient)
    paths: list[str] = []
    current = Instrument(
        symbol="BR",
        secid="BRV6",
        board="RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        root_symbol="BR",
        source="MOEX_ISS",
    )

    def request(path, params):
        paths.append(path)
        return _response(_empty_candles())

    client._request = request
    series = client.fetch_candles(
        current,
        timeframe="D1",
        from_date="2026-09-01",
        till_date="2026-09-02",
    )

    assert series.count == 0
    assert not any(path.startswith("/history/") for path in paths)
