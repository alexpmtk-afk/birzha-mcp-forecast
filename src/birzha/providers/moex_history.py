"""Historical MOEX futures contract resolution for causal research/backfills."""

from __future__ import annotations

from datetime import date, timedelta
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
        """Resolve the real liquid contract separately for each historical weekday.

        MOEX futures history treats ``date`` as the historical selector; ranged
        ``from/till`` parameters do not provide a multi-day contract timeline.
        Weekends are skipped before requesting and exchange holidays naturally
        return no matching rows. All requests still pass through the shared
        provider governor, so long backfills remain rate-limited and bounded.
        """
        root = root_symbol.strip()
        if not root:
            raise ValueError("root_symbol must be non-empty")
        if from_date > till_date:
            raise ValueError("from_date must not be after till_date")

        days: list[date] = []
        cursor = from_date
        while cursor <= till_date:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        if not days:
            return ()

        path = "/history/engines/futures/markets/forts/securities.json"
        requests = [
            (
                path,
                {
                    "iss.meta": "off",
                    "iss.only": "history",
                    "history.columns": (
                        "TRADEDATE,SECID,BOARDID,ASSETCODE,VALUE,VOLUME,"
                        "OPENPOSITIONVALUE,OPENPOSITION,SHORTNAME,LASTTRADEDATE"
                    ),
                    "date": day.isoformat(),
                    "assetcode": root,
                },
            )
            for day in days
        ]
        responses = self._client._request_many(requests)  # noqa: SLF001
        resolved: list[tuple[date, Instrument]] = []
        for day, response in zip(days, responses, strict=True):
            payload = response.json()
            rows = self._client._table(payload, "history")  # noqa: SLF001
            exact_rows = []
            for row in rows:
                raw = _text(row, "TRADEDATE")
                if raw and raw[:10] != day.isoformat():
                    continue
                asset = _text(row, "ASSETCODE")
                secid = _text(row, "SECID")
                if asset.lower() == root.lower() or (not asset and secid.lower().startswith(root.lower())):
                    exact_rows.append(row)
            if not exact_rows:
                continue
            resolved.append((day, _pick_instrument(root, day, exact_rows)))
        return tuple(resolved)


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
