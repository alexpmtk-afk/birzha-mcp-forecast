"""Official MOEX ISS trading-session calendar provider."""

from __future__ import annotations

from datetime import date

from birzha.providers.moex_iss import MoexIssClient, MoexIssError


MAX_CALENDAR_PAGES = 100
ACTIVITY_COLUMNS = ("NUMTRADES", "VOLUME", "VALUE")


class MoexTradingCalendar:
    """Return actual trading dates from exact-security MOEX history rows.

    MOEX history can contain placeholder/zero-activity rows on dates when the
    security did not actually trade.  Such a row is not sufficient evidence of
    a trading session: when activity fields are present we require positive
    NUMTRADES, VOLUME or VALUE.  This keeps the expected-session calendar aligned
    with the candle endpoint and prevents false missing-candle failures.
    """

    def __init__(self, client: MoexIssClient) -> None:
        self._client = client

    def dates(
        self,
        *,
        engine: str,
        market: str,
        board: str,
        security: str,
        from_date: date,
        till_date: date,
    ) -> tuple[date, ...]:
        if from_date > till_date:
            raise ValueError("from_date must not be after till_date")
        if not security.strip():
            raise ValueError("security must be non-empty")

        path = (
            f"/history/engines/{engine}/markets/{market}/boards/{board}/"
            f"securities/{security}.json"
        )
        base_params = {
            "iss.meta": "off",
            "iss.only": "history,history.cursor",
            "history.columns": "TRADEDATE,NUMTRADES,VOLUME,VALUE",
            "from": from_date.isoformat(),
            "till": till_date.isoformat(),
        }
        result: set[date] = set()
        seen_pages: set[tuple[int, str, str]] = set()
        start = 0
        for _ in range(MAX_CALENDAR_PAGES):
            payload = self._client._request(  # noqa: SLF001 - provider-internal collaboration
                path, {**base_params, "start": start}
            ).json()
            page = self._client._table(payload, "history")  # noqa: SLF001
            if page:
                signature = _page_signature(page)
                if signature in seen_pages:
                    raise MoexIssError(
                        f"MOEX history calendar pagination stalled for "
                        f"{security}: repeated page at start={start}"
                    )
                seen_pages.add(signature)

            for row in page:
                if not _has_trading_activity(row):
                    continue
                raw = row.get("TRADEDATE") or row.get("tradedate")
                if not raw:
                    continue
                try:
                    day = date.fromisoformat(str(raw)[:10])
                except ValueError:
                    continue
                if from_date <= day <= till_date:
                    result.add(day)

            cursor_rows = (
                self._client._table(payload, "history.cursor")  # noqa: SLF001
                if "history.cursor" in payload
                else []
            )
            if cursor_rows:
                cursor = cursor_rows[0]
                total = (
                    _integer(cursor, "TOTAL")
                    or _integer(cursor, "total")
                    or (start + len(page))
                )
                page_size = (
                    _integer(cursor, "PAGESIZE")
                    or _integer(cursor, "pagesize")
                    or len(page)
                )
                if start + len(page) >= total or page_size <= 0 or not page:
                    return tuple(sorted(result))
                next_start = start + page_size
                if next_start <= start:
                    raise MoexIssError(
                        f"MOEX history calendar cursor did not advance for {security}"
                    )
                start = next_start
                continue
            if not page or len(page) < 100:
                return tuple(sorted(result))
            start += len(page)

        raise MoexIssError(
            f"MOEX history calendar pagination exceeded safe "
            f"max_pages={MAX_CALENDAR_PAGES} for {security}"
        )


def _has_trading_activity(row: dict[str, object]) -> bool:
    """Return whether a MOEX history row proves an actual trading session.

    Older unit-test doubles may expose only TRADEDATE.  Absence of all activity
    columns therefore retains legacy row-as-session behaviour for compatibility.
    Real MOEX rows requested by this provider include the activity columns; when
    present, all-null/all-zero activity is treated as a non-session.
    """
    present = False
    for key in ACTIVITY_COLUMNS:
        candidates = (key, key.lower())
        if any(candidate in row for candidate in candidates):
            present = True
        value = next((row[candidate] for candidate in candidates if candidate in row), None)
        try:
            if value is not None and float(value) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return not present


def _page_signature(page: list[dict[str, object]]) -> tuple[int, str, str]:
    if not page:
        return (0, "", "")
    first = str(page[0].get("TRADEDATE") or page[0].get("tradedate") or "")
    last = str(page[-1].get("TRADEDATE") or page[-1].get("tradedate") or "")
    return (len(page), first, last)


def _integer(row: dict[str, object], key: str) -> int | None:
    value = row.get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
