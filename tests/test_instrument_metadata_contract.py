from datetime import date
import json

from birzha.providers.moex_iss import IssResponse, MoexIssClient
from birzha.providers.moex_resolver import MoexDirectInstrumentResolver


def _response(payload: dict) -> IssResponse:
    return IssResponse(
        status_code=200,
        headers={},
        body=json.dumps(payload).encode("utf-8"),
    )


def test_direct_equity_metadata_populates_tick_lot_and_currency() -> None:
    client = object.__new__(MoexIssClient)

    boards = {
        "boards": {
            "columns": [
                "secid","boardid","title","market","engine","is_primary",
                "listed_from","listed_till","has_candles"
            ],
            "data": [
                ["SBER","TQBR","Сбербанк","shares","stock",1,"2007-07-20",None,1]
            ],
        }
    }
    security = {
        "securities": {
            "columns": [
                "SECID","SHORTNAME","SECNAME","LOTSIZE","MINSTEP","CURRENCYID"
            ],
            "data": [
                ["SBER","Сбербанк","Сбербанк ПАО",10,0.01,"SUR"]
            ],
        }
    }

    def request(path, params):
        if path == "/securities/SBER.json":
            return _response(boards)
        if path == "/engines/stock/markets/shares/boards/TQBR/securities/SBER.json":
            return _response(security)
        raise AssertionError(path)

    client._request = request
    resolver = MoexDirectInstrumentResolver(client)

    instrument = resolver.resolve("SBER")

    assert instrument is not None
    assert instrument.currency == "SUR"
    assert instrument.tick_size == 0.01
    assert instrument.contract_multiplier == 10.0
    assert instrument.tick_value == 0.1
    assert instrument.calendar_id == "MOEX:STOCK:SHARES:TQBR"
    assert instrument.session_profile == "MOEX_EQUITIES"


def test_active_future_metadata_populates_contract_terms() -> None:
    client = object.__new__(MoexIssClient)
    payload = {
        "securities": {
            "columns": [
                "SECID","SHORTNAME","NAME","BOARDID","LASTTRADEDATE","ASSETCODE",
                "MINSTEP","STEPPRICE","LOTVOLUME","CURRENCYID","LASTDELDATE"
            ],
            "data": [
                [
                    "SiZ6","Si-12.26","USD/RUB 12.26","RFUD","2026-12-17","Si",
                    1.0,1.0,1000.0,"SUR","2026-12-17"
                ]
            ],
        },
        "marketdata": {
            "columns": ["SECID","LAST","VALTODAY","OPENPOSITION","NUMTRADES"],
            "data": [["SiZ6",81.0,1000000,300000,4000]],
        },
    }
    client._request = lambda *args, **kwargs: _response(payload)

    instrument = client.resolve_active_future("Si", as_of=date(2026, 10, 3))

    assert instrument.secid == "SiZ6"
    assert instrument.currency == "SUR"
    assert instrument.tick_size == 1.0
    assert instrument.tick_value == 1.0
    assert instrument.contract_multiplier == 1000.0
    assert instrument.expiration_date == "2026-12-17"
    assert instrument.settlement_date == "2026-12-17"
    assert instrument.calendar_id == "MOEX:FUTURES:FORTS:RFUD"
    assert instrument.session_profile == "MOEX_FORTS"
