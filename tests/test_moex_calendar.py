from datetime import date

from birzha.providers.moex_calendar import MoexTradingCalendar


SESSIONS = {
    "2026-01-05",
    "2026-01-06",
    "2026-01-08",
    "2026-01-09",
}


class Response:
    def __init__(self, tradedate: str | None):
        self.tradedate = tradedate

    def json(self):
        return {
            "history": {
                "columns": ["TRADEDATE"],
                "data": [[self.tradedate]] if self.tradedate else [],
            }
        }


class Client:
    def __init__(self):
        self.requested_dates: list[str] = []

    def _request(self, path, params):
        assert path == "/history/engines/futures/markets/forts/boards/RFUD/securities.json"
        assert params["iss.only"] == "history"
        assert params["history.columns"] == "TRADEDATE"
        assert params["limit"] == 1
        requested = params["date"]
        self.requested_dates.append(requested)
        return Response(requested if requested in SESSIONS else None)

    @staticmethod
    def _table(payload, name):
        table = payload[name]
        return [dict(zip(table["columns"], row)) for row in table["data"]]


def test_calendar_uses_exchange_history_not_weekday_arithmetic():
    client = Client()
    days = MoexTradingCalendar(client).dates(
        engine="futures",
        market="forts",
        board="RFUD",
        from_date=date(2026, 1, 1),
        till_date=date(2026, 1, 10),
    )

    # 7 January is a weekday but the exchange returned no history row, so it
    # must remain excluded. Weekends are not even probed.
    assert days == (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 8),
        date(2026, 1, 9),
    )
    assert "2026-01-07" in client.requested_dates
    assert "2026-01-03" not in client.requested_dates
    assert "2026-01-04" not in client.requested_dates
