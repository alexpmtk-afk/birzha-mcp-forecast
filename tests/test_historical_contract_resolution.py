"""Historical contract lookup must be driven by T-1 completed tradedate.

This deliberately replaces the former same-day VALUE/OI-ranking expectation:
using target-day statistics to choose that day's contract leaked future data.
"""
from datetime import date

from birzha.providers.moex_history import MoexHistoricalFutureResolver


def result_row(day, secid, volume, *, value=1, expiry="2026-09-17"):
    return {
        "TRADEDATE": day,
        "SECID": secid,
        "ASSETCODE": "Si",
        "BOARDID": "RFUD",
        "VOLUME": volume,
        "VALUE": value,
        "LASTTRADEDATE": expiry,
        "SHORTNAME": secid,
    }


class Response:
    def __init__(self, rows):
        self.rows = rows

    def json(self):
        return {"rows": self.rows}


class Client:
    def __init__(self, by_day):
        self.by_day = by_day
        self.requested_dates = []

    def _request(self, path, params):
        assert path == "/history/engines/futures/markets/forts/securities.json"
        assert params["assetcode"] == "Si"
        day = params["date"]
        self.requested_dates.append(day)
        return Response(self.by_day.get(day, []))

    @staticmethod
    def _table(payload, name):
        assert name == "history"
        return payload["rows"]


def fixture():
    return {
        "2026-06-09": [
            result_row("2026-06-09", "SiM6", 500, value=1000000, expiry="2026-06-18"),
            result_row("2026-06-09", "SiU6", 20, value=1),
        ],
        "2026-06-10": [
            result_row("2026-06-10", "SiM6", 50, value=1000000, expiry="2026-06-18"),
            result_row("2026-06-10", "SiU6", 20, value=1),
        ],
        "2026-06-11": [
            result_row("2026-06-11", "SiM6", 30, value=900000, expiry="2026-06-18"),
            result_row("2026-06-11", "SiU6", 90, value=1),
        ],
        "2026-06-12": [
            result_row("2026-06-12", "SiM6", 100, value=1000000, expiry="2026-06-18"),
            result_row("2026-06-12", "SiU6", 200, value=1),
        ],
        "2026-06-15": [],
    }


def test_historical_resolver_uses_previous_trading_session_not_same_day():
    client = Client(fixture())
    instrument = MoexHistoricalFutureResolver(client).resolve("Si", date(2026, 6, 12))
    # Previous 11 June D1: SiU6 VOLUME=90, SiM6 VOLUME=30,
    # irrespective of huge old-contract VALUE.
    assert instrument.secid == "SiU6"
    assert instrument.root_symbol == "Si"
    assert instrument.source == "MOEX_ISS_HISTORY_T_MINUS_1_RECONSTRUCTED"
    assert instrument.roll_policy == "MOEX_T_MINUS_1_VOLUME_EXPIRY_V1"
    assert instrument.board == "RFUD"
    assert "2026-06-12" not in client.requested_dates


def test_timeline_tracks_switch_from_prior_session_volume():
    client = Client(fixture())
    timeline = MoexHistoricalFutureResolver(client).timeline(
        "Si", date(2026, 6, 10), date(2026, 6, 15)
    )
    assert [(d.isoformat(), c.secid) for d, c in timeline] == [
        ("2026-06-10", "SiM6"),
        ("2026-06-11", "SiM6"),
        ("2026-06-12", "SiU6"),
    ]


def test_timeline_reuses_resolved_and_empty_day_cache():
    client = Client(fixture())
    resolver = MoexHistoricalFutureResolver(client)
    first = resolver.timeline("Si", date(2026, 6, 10), date(2026, 6, 15))
    already = list(client.requested_dates)
    second = resolver.timeline("Si", date(2026, 6, 10), date(2026, 6, 15))
    assert second == first
    assert client.requested_dates == already
