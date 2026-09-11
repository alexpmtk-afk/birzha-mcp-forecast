from datetime import date

import pytest

from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.providers.moex_iss import MoexIssError


class Response:
    def json(self):
        return {
            "history": {
                "columns": ["TRADEDATE"],
                "data": [
                    ["2026-01-05"],
                    ["2026-01-06"],
                    ["2026-01-08"],
                    ["2026-01-09"],
                ],
            }
        }


class Client:
    def _request(self, path, params):
        assert path == (
            "/history/engines/futures/markets/forts/boards/RFUD/"
            "securities/SiH6.json"
        )
        assert params["from"] == "2026-01-01"
        assert params["till"] == "2026-01-10"
        assert params["history.columns"] == "TRADEDATE"
        return Response()

    @staticmethod
    def _table(payload, name):
        if name not in payload:
            return []
        table = payload[name]
        return [dict(zip(table["columns"], row)) for row in table["data"]]


def test_calendar_uses_exact_security_history_rows():
    days = MoexTradingCalendar(Client()).dates(
        engine="futures",
        market="forts",
        board="RFUD",
        security="SiH6",
        from_date=date(2026, 1, 1),
        till_date=date(2026, 1, 10),
    )

    # A weekday without a history row (7 January) is not manufactured as a
    # session. Only dates actually returned by MOEX become horizon sessions.
    assert days == (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 8),
        date(2026, 1, 9),
    )


class _RepeatingResponse:
    def json(self):
        return {
            "history": {
                "columns": ["TRADEDATE"],
                "data": [["2026-01-05"] for _ in range(100)],
            }
        }


class _RepeatingClient:
    def __init__(self):
        self.calls = 0

    def _request(self, path, params):
        self.calls += 1
        return _RepeatingResponse()

    @staticmethod
    def _table(payload, name):
        if name not in payload:
            return []
        table = payload[name]
        return [dict(zip(table["columns"], row)) for row in table["data"]]


def test_calendar_repeated_full_page_fails_closed_instead_of_looping():
    client = _RepeatingClient()

    with pytest.raises(MoexIssError, match="pagination stalled"):
        MoexTradingCalendar(client).dates(
            engine="futures",
            market="forts",
            board="RFUD",
            security="SiH6",
            from_date=date(2026, 1, 1),
            till_date=date(2026, 12, 31),
        )

    assert client.calls == 2
