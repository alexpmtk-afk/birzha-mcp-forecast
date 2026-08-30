from datetime import date

from birzha.providers.moex_calendar import MoexTradingCalendar


class Response:
    def json(self):
        return {
            "dates": {
                "columns": ["TRADEDATE"],
                "data": [["2026-01-05"], ["2026-01-06"], ["2026-01-08"]],
            }
        }


class Client:
    def _request(self, path, params):
        assert path == "/history/engines/futures/markets/forts/boards/RFUD/dates.json"
        assert params["from"] == "2026-01-01"
        assert params["till"] == "2026-01-10"
        return Response()

    @staticmethod
    def _table(payload, name):
        table = payload[name]
        return [dict(zip(table["columns"], row)) for row in table["data"]]


def test_calendar_uses_exchange_dates_not_weekday_arithmetic():
    days = MoexTradingCalendar(Client()).dates(
        engine="futures",
        market="forts",
        board="RFUD",
        from_date=date(2026, 1, 1),
        till_date=date(2026, 1, 10),
    )
    assert days == (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 8))
