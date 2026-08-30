"""Official MOEX ISS trading-date calendar provider."""

from __future__ import annotations

from datetime import date

from birzha.providers.moex_iss import MoexIssClient


class MoexTradingCalendar:
    def __init__(self, client: MoexIssClient) -> None:
        self._client = client

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
        payload = self._client._request(  # noqa: SLF001 - provider-internal collaboration
            f"/history/engines/{engine}/markets/{market}/boards/{board}/dates.json",
            {
                "iss.meta": "off",
                "from": from_date.isoformat(),
                "till": till_date.isoformat(),
            },
        ).json()
        rows = self._client._table(payload, "dates")  # noqa: SLF001
        result: list[date] = []
        for row in rows:
            raw = row.get("TRADEDATE") or row.get("tradedate")
            if not raw:
                continue
            try:
                day = date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
            if from_date <= day <= till_date:
                result.append(day)
        return tuple(sorted(set(result)))
