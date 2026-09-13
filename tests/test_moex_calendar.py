from datetime import date

import pytest

from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.providers.moex_iss import MoexIssError


class Response:
    def json(self):
        return {
            "history": {
                "columns": ["TRADEDATE", "NUMTRADES", "VOLUME", "VALUE"],
                "data": [
                    ["2026-01-05", 10, 100, 1000],
                    ["2026-01-06", 5, 50, 500],
                    ["2026-01-08", 1, 10, 100],
                    ["2026-01-09", 2, 20, 200],
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
        assert params["history.columns"] == "TRADEDATE,NUMTRADES,VOLUME,VALUE"
        return Response()

    @staticmethod
    def _table(payload, name):
        if name not in payload:
            return []
        table = payload[name]
        return [dict(zip(table["columns"], row)) for row in table["data"]]


def test_calendar_uses_exact_security_history_rows_with_trading_activity():
    days = MoexTradingCalendar(Client()).dates(
        engine="futures",
        market="forts",
        board="RFUD",
        security="SiH6",
        from_date=date(2026, 1, 1),
        till_date=date(2026, 1, 10),
    )

    assert days == (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 8),
        date(2026, 1, 9),
    )


class _ZeroActivityResponse:
    def json(self):
        return {
            "history": {
                "columns": ["TRADEDATE", "NUMTRADES", "VOLUME", "VALUE"],
                "data": [
                    ["2022-01-06", 20, 200, 2000],
                    ["2022-01-07", 0, 0, 0],
                    ["2022-02-23", None, None, None],
                    ["2022-03-24", 1, 10, 100],
                ],
            }
        }


class _ZeroActivityClient:
    def _request(self, path, params):
        return _ZeroActivityResponse()

    @staticmethod
    def _table(payload, name):
        if name not in payload:
            return []
        table = payload[name]
        return [dict(zip(table["columns"], row)) for row in table["data"]]


def test_calendar_excludes_zero_activity_history_rows():
    days = MoexTradingCalendar(_ZeroActivityClient()).dates(
        engine="stock",
        market="shares",
        board="TQBR",
        security="SBER",
        from_date=date(2022, 1, 1),
        till_date=date(2022, 3, 31),
    )

    assert days == (date(2022, 1, 6), date(2022, 3, 24))


class _LegacyDateOnlyResponse:
    def json(self):
        return {
            "history": {
                "columns": ["TRADEDATE"],
                "data": [["2026-01-05"]],
            }
        }


class _LegacyDateOnlyClient:
    def _request(self, path, params):
        return _LegacyDateOnlyResponse()

    @staticmethod
    def _table(payload, name):
        if name not in payload:
            return []
        table = payload[name]
        return [dict(zip(table["columns"], row)) for row in table["data"]]


def test_calendar_keeps_legacy_date_only_test_double_compatible():
    days = MoexTradingCalendar(_LegacyDateOnlyClient()).dates(
        engine="futures",
        market="forts",
        board="RFUD",
        security="SiH6",
        from_date=date(2026, 1, 1),
        till_date=date(2026, 1, 10),
    )
    assert days == (date(2026, 1, 5),)


class _RepeatingResponse:
    def json(self):
        return {
            "history": {
                "columns": ["TRADEDATE", "NUMTRADES", "VOLUME", "VALUE"],
                "data": [["2026-01-05", 1, 1, 1] for _ in range(100)],
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
