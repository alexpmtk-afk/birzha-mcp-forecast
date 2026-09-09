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


class TimelineClient:
    def __init__(self):
        self.requested_dates = []

    def _request_many(self, requests):
        responses = []
        data_by_day = {
            "2026-06-10": [["2026-06-10", "SiM6", "RFUD", "Si", 500, 50, 300], ["2026-06-10", "SiU6", "RFUD", "Si", 100, 20, 80]],
            "2026-06-11": [["2026-06-11", "SiM6", "RFUD", "Si", 200, 30, 120], ["2026-06-11", "SiU6", "RFUD", "Si", 900, 90, 500]],
            "2026-06-12": [["2026-06-12", "SiU6", "RFUD", "Si", 1000, 100, 600]],
            "2026-06-15": [],
        }
        for path, params in requests:
            assert path == "/history/engines/futures/markets/forts/securities.json"
            assert "from" not in params and "till" not in params
            assert params["assetcode"] == "Si"
            day = params["date"]
            self.requested_dates.append(day)
            responses.append(Response({
                "history": {
                    "columns": ["TRADEDATE", "SECID", "BOARDID", "ASSETCODE", "VALUE", "VOLUME", "OPENPOSITIONVALUE"],
                    "data": data_by_day[day],
                }
            }))
        return responses

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


def test_timeline_tracks_real_rollover_by_daily_liquidity():
    client = TimelineClient()
    timeline = MoexHistoricalFutureResolver(client).timeline(
        "Si", date(2026, 6, 10), date(2026, 6, 15)
    )
    assert [(day.isoformat(), instrument.secid) for day, instrument in timeline] == [
        ("2026-06-10", "SiM6"),
        ("2026-06-11", "SiU6"),
        ("2026-06-12", "SiU6"),
    ]
    assert client.requested_dates == ["2026-06-10", "2026-06-11", "2026-06-12", "2026-06-15"]


def test_timeline_reuses_resolved_and_empty_days_from_cache():
    client = TimelineClient()
    resolver = MoexHistoricalFutureResolver(client)
    first = resolver.timeline("Si", date(2026, 6, 10), date(2026, 6, 15))
    requested_after_first = list(client.requested_dates)
    second = resolver.timeline("Si", date(2026, 6, 10), date(2026, 6, 15))

    assert second == first
    assert client.requested_dates == requested_after_first
