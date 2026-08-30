"""Resolve directly listed MOEX securities without symbol-specific domain rules."""

from __future__ import annotations

from typing import Any

from birzha.domain.market import AssetClass, Instrument
from birzha.providers.moex_iss import MoexIssClient


class MoexDirectInstrumentResolver:
    """Resolve exact SECID (e.g. SBER) from MOEX's security boards table."""

    def __init__(self, client: MoexIssClient) -> None:
        self._client = client

    def resolve(self, symbol: str) -> Instrument | None:
        secid = symbol.strip().upper()
        if not secid:
            raise ValueError("symbol must be non-empty")
        payload = self._client._request(  # noqa: SLF001 - provider-internal collaboration
            f"/securities/{secid}.json",
            {
                "iss.meta": "off",
                "iss.only": "boards",
                "boards.columns": (
                    "secid,boardid,title,market,engine,is_primary,"
                    "listed_from,listed_till,has_candles"
                ),
            },
        ).json()
        rows = self._client._table(payload, "boards")  # noqa: SLF001
        candidates = [row for row in rows if _text(row, "secid") == secid]
        if not candidates:
            return None

        # Prefer the official primary board and a board with candle history.
        candidates.sort(
            key=lambda row: (
                _integer(row, "is_primary") == 1,
                _integer(row, "has_candles") == 1,
                _text(row, "boardid"),
            ),
            reverse=True,
        )
        row = candidates[0]
        engine = _text(row, "engine").lower()
        market = _text(row, "market").lower()
        board = _text(row, "boardid")
        if not engine or not market or not board:
            return None

        return Instrument(
            symbol=secid,
            secid=secid,
            board=board,
            engine=engine,
            market=market,
            asset_class=_asset_class(engine, market),
            name=_text(row, "title") or secid,
            root_symbol=None,
            last_trade_date=_text(row, "listed_till")[:10] or None,
        )


def _first(row: dict[str, Any], key: str) -> object | None:
    for candidate in (key, key.lower(), key.upper()):
        if candidate in row and row[candidate] is not None:
            return row[candidate]
    return None


def _text(row: dict[str, Any], key: str) -> str:
    value = _first(row, key)
    return str(value).strip() if value is not None else ""


def _integer(row: dict[str, Any], key: str) -> int | None:
    value = _first(row, key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _asset_class(engine: str, market: str) -> AssetClass:
    pair = (engine.lower(), market.lower())
    if pair == ("stock", "shares"):
        return "equity"
    if pair == ("stock", "index"):
        return "index"
    if pair == ("currency", "selt"):
        return "fx"
    if pair == ("futures", "forts"):
        return "future"
    return "unknown"
