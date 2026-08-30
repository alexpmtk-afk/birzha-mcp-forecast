from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.providers.moex_history import MoexHistoricalFutureResolver


@dataclass
class Response:
    payload: dict

    def json(self):
        return self.payload


class Client:
    def _request(self, path, params):
        assert path == "/history/engines/futures/markets/forts/securities.json"
        assert params["date"] == "2026-06-15"
        assert params["assetcode"] == "Si"
        return Response({
            "history": {
                "columns": ["SECID", "BOARDID", "ASSETCODE", "VALUE", "VOLUME", "OPENPOSITIONVALUE"],
                "data": [
                    ["SiM6", "RFUD", "Si", 120_000_000, 1000, 50_000_000],
                    ["SiU6", "RFUD", "Si", 900_000_000, 9000, 350_000_000],
                    ["BRN6", "RFUD", "BR", 2_000_000_000, 12000, 400_000_000],
                ],
            }
        })

    @staticmethod
    def _table(payload, name):
        table = payload[name]
        return [dict(zip(table["columns"], row)) for row in table["data"]]


def test_historical_resolver_uses_liquidity_on_requested_date():
    instrument = MoexHistoricalFutureResolver(Client()).resolve("Si", date(2026, 6, 15))
    assert instrument.secid == "SiU6"
    assert instrument.root_symbol == "Si"
    assert instrument.source == "MOEX_ISS_HISTORY"
    assert instrument.board == "RFUD"
