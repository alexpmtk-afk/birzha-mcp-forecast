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
        path = f"/history/engines/{engine}/markets/{market}/boards/{board}/dates.json"
        base_params = {
            "iss.meta": "off",
            "from": from_date.isoformat(),
            "till": till_date.isoformat(),
        }
        rows: list[dict[str, object]] = []
        start = 0
        while True:
            payload = self._client._request(  # noqa: SLF001 - provider-internal collaboration
                path,
                {**base_params, "start": start},
            ).json()
            page = self._client._table(payload, "dates")  # noqa: SLF001
            rows.extend(page)
            cursor_rows = (
                self._client._table(payload, "dates.cursor")  # noqa: SLF001
                if "dates.cursor" in payload
                else []
            )
            if cursor_rows:
                cursor = cursor_rows[0]
                total = _integer(cursor, "TOTAL") or _integer(cursor, "total") or len(rows)
                page_size = _integer(cursor, "PAGESIZE") or _integer(cursor, "pagesize") or len(page)
                if len(rows) >= total or page_size <= 0:
                    break
                start += page_size
                continue
            if not page:
                break
            start += len(page)
            if len(page) < 100:
                break

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


def _integer(row: dict[str, object], key: str) -> int | None:
    value = row.get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
