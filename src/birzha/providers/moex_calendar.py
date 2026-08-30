"""Official MOEX ISS trading-session calendar provider."""

from __future__ import annotations

from datetime import date, timedelta

from birzha.providers.moex_iss import MoexIssClient


class MoexTradingCalendar:
    """Return actual exchange trading dates for one MOEX board.

    ISS ``.../boards/{board}/dates`` describes the available history interval,
    not a row-per-session calendar.  We therefore probe the official board
    history for candidate weekdays and accept a date only when MOEX returns a
    history row for that exact date.  Weekdays are merely request candidates;
    the exchange response is the source of truth, so holidays remain excluded.
    The query asks for one TRADEDATE row only, keeping the safety budget small.
    """

    def __init__(self, client: MoexIssClient) -> None:
        self._client = client
        self._cache: dict[tuple[str, str, str, date], bool] = {}

    def dates(
        self,
        *,
        engine: str,
        market: str,
        board: str,
        from_date: date,
        till_date: date,
    ) -> tuple[date, ...]:
        if from_date > till_date:
            raise ValueError("from_date must not be after till_date")
        result: list[date] = []
        current = from_date
        while current <= till_date:
            if current.weekday() < 5 and self._is_exchange_session(
                engine=engine, market=market, board=board, day=current
            ):
                result.append(current)
            current += timedelta(days=1)
        return tuple(result)

    def _is_exchange_session(self, *, engine: str, market: str, board: str, day: date) -> bool:
        key = (engine, market, board, day)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = f"/history/engines/{engine}/markets/{market}/boards/{board}/securities.json"
        payload = self._client._request(  # noqa: SLF001 - provider-internal collaboration
            path,
            {
                "iss.meta": "off",
                "iss.only": "history",
                "history.columns": "TRADEDATE",
                "date": day.isoformat(),
                "limit": 1,
                "start": 0,
            },
        ).json()
        rows = self._client._table(payload, "history")  # noqa: SLF001
        is_session = False
        for row in rows:
            raw = row.get("TRADEDATE") or row.get("tradedate")
            if raw and str(raw)[:10] == day.isoformat():
                is_session = True
                break
        self._cache[key] = is_session
        return is_session
