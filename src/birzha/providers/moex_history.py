"""Historical MOEX futures contract resolution for causal walk-forward tests.

The resolver queries the official ISS history-by-market endpoint for the exact
trade date and selects the most liquid contract for the requested asset code.
This prevents historical forecasts from accidentally using today's active
contract.
"""

from __future__ import annotations

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
            {
                "iss.meta": "off",
                "date": as_of.isoformat(),
                "assetcode": root,
            },
        ).json()
        rows = self._client._table(payload, "history")  # noqa: SLF001
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
            raise MoexIssError(
                f"No historical MOEX futures contract found for {root_symbol!r} on {as_of.isoformat()}"
            )
        candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
        _, _, _, secid, row = candidates[0]
        board = _text(row, "BOARDID") or "RFUD"
        return Instrument(
            symbol=root,
            secid=secid,
            board=board,
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
