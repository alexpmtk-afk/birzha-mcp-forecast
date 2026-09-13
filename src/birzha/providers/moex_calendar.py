"""Official MOEX ISS price-session calendar provider."""

from __future__ import annotations

import math
from datetime import date

from birzha.providers.moex_iss import MoexIssClient, MoexIssError


MAX_CALENDAR_PAGES = 100
ACTIVITY_COLUMNS = ("NUMTRADES", "VOLUME", "VALUE")
PRICE_COLUMNS = ("OPEN", "CLOSE", "HIGH", "LOW", "WAPRICE")


class MoexTradingCalendar:
    """Return price-bearing trading dates from exact-security MOEX history rows.

    MOEX history can contain placeholder/zero-activity rows on dates when the
    security did not actually trade.  It can also contain rare rows with
    positive activity but no usable price and no candle at any interval.  Such
    rows cannot support price history and must not become expected candle dates.

    Real provider rows therefore require both positive trading activity and at
    least one positive finite price.  Legacy unit-test doubles that omit the
    corresponding column group retain row-as-session compatibility.
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
            "history.columns": (
                "TRADEDATE,NUMTRADES,VOLUME,VALUE,OPEN,CLOSE,HIGH,LOW,WAPRICE"
            ),
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
                if not _has_trading_activity(row) or not _has_usable_price(row):
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
    """Return whether a MOEX history row proves actual trading activity."""
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


def _has_usable_price(row: dict[str, object]) -> bool:
    """Return whether a history row can support a real price candle.

    The BRJ0 2019-06-25 anomaly is the motivating case: MOEX reports one trade,
    volume and value, while OPEN/CLOSE/HIGH/LOW are null, WAPRICE is zero and
    D1/H1/M1 candle endpoints all return no rows.  Treating that row as an
    expected price session would force fabrication or a permanent false gap.
    """
    present = False
    for key in PRICE_COLUMNS:
        candidates = (key, key.lower())
        if any(candidate in row for candidate in candidates):
            present = True
        value = next((row[candidate] for candidate in candidates if candidate in row), None)
        try:
            numeric = float(value) if value is not None else None
        except (TypeError, ValueError):
            continue
        if numeric is not None and math.isfinite(numeric) and numeric > 0:
            return True
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
