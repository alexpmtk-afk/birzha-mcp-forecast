"""Historical MOEX futures contract resolution for causal research/backfills."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from birzha.domain.market import Instrument
from birzha.providers.moex_iss import MoexIssClient, MoexIssError


class MoexHistoricalFutureResolver:
    def __init__(self, client: MoexIssClient) -> None:
        self._client = client

    def resolve(self, root_symbol: str, as_of: date) -> Instrument:
        root = root_symbol.strip()
        if not root:
            raise ValueError("root_symbol must be non-empty")
        payload = self._client._request(  # noqa: SLF001 - provider-internal collaboration
            "/history/engines/futures/markets/forts/securities.json",
            {"iss.meta": "off", "date": as_of.isoformat(), "assetcode": root},
        ).json()
        rows = self._client._table(payload, "history")  # noqa: SLF001
        return _pick_instrument(root, as_of, rows)

    def timeline(self, root_symbol: str, from_date: date, till_date: date) -> tuple[tuple[date, Instrument], ...]:
        """Resolve the most liquid real contract for every available trade date.

        One ranged, paginated ISS history scan is used instead of one resolver
        request per day. This is the canonical input for rollover-safe backfills.
        """
        root = root_symbol.strip()
        if not root:
            raise ValueError("root_symbol must be non-empty")
        if from_date > till_date:
            raise ValueError("from_date must not be after till_date")

        grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
        start = 0
        while True:
            payload = self._client._request(  # noqa: SLF001
                "/history/engines/futures/markets/forts/securities.json",
                {
                    "iss.meta": "off",
                    "iss.only": "history,history.cursor",
                    "history.columns": (
                        "TRADEDATE,SECID,BOARDID,ASSETCODE,VALUE,VOLUME,"
                        "OPENPOSITIONVALUE,OPENPOSITION,SHORTNAME,LASTTRADEDATE"
                    ),
                    "assetcode": root,
                    "from": from_date.isoformat(),
                    "till": till_date.isoformat(),
                    "start": start,
                },
            ).json()
            page = self._client._table(payload, "history")  # noqa: SLF001
            for row in page:
                raw = _text(row, "TRADEDATE")
                try:
                    trade_date = date.fromisoformat(raw[:10])
                except ValueError:
                    continue
                if from_date <= trade_date <= till_date:
                    grouped[trade_date].append(row)

            cursor_rows = (
                self._client._table(payload, "history.cursor")  # noqa: SLF001
                if "history.cursor" in payload
                else []
            )
            if cursor_rows:
                cursor = cursor_rows[0]
                total = _integer(cursor, "TOTAL") or _integer(cursor, "total") or (start + len(page))
                page_size = _integer(cursor, "PAGESIZE") or _integer(cursor, "pagesize") or len(page)
                if not page or page_size <= 0 or start + len(page) >= total:
                    break
                start += page_size
                continue
            if not page:
                break
            start += len(page)
            if len(page) < 100:
                break

        return tuple((day, _pick_instrument(root, day, grouped[day])) for day in sorted(grouped))


def _pick_instrument(root: str, as_of: date, rows: list[dict[str, Any]]) -> Instrument:
    root_lower = root.lower()
    candidates: list[tuple[float, float, float, str, dict[str, Any]]] = []
    for row in rows:
        secid = _text(row, "SECID")
        asset = _text(row, "ASSETCODE")
        if not secid:
            continue
        if asset and asset.lower() != root_lower:
            continue
        if not asset and not secid.lower().startswith(root_lower):
            continue
        value = _number(row, "VALUE") or 0.0
        oi_value = _number(row, "OPENPOSITIONVALUE") or _number(row, "OPENPOSITION") or 0.0
        volume = _number(row, "VOLUME") or 0.0
        candidates.append((value, oi_value, volume, secid, row))
    if not candidates:
        raise MoexIssError(f"No historical MOEX futures contract found for {root!r} on {as_of.isoformat()}")
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    _, _, _, secid, row = candidates[0]
    return Instrument(
        symbol=root,
        secid=secid,
        board=_text(row, "BOARDID") or "RFUD",
        engine="futures",
        market="forts",
        asset_class="future",
        name=_text(row, "SHORTNAME") or secid,
        root_symbol=root,
        last_trade_date=_text(row, "LASTTRADEDATE")[:10] or None,
        source="MOEX_ISS_HISTORY",
    )


def _first(row: dict[str, Any], key: str) -> object | None:
    for candidate in (key, key.lower(), key.upper()):
        if candidate in row and row[candidate] is not None:
            return row[candidate]
    return None


def _text(row: dict[str, Any], key: str) -> str:
    value = _first(row, key)
    return str(value).strip() if value is not None else ""


def _number(row: dict[str, Any], key: str) -> float | None:
    value = _first(row, key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _integer(row: dict[str, Any], key: str) -> int | None:
    value = _first(row, key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
