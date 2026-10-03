"""MOEX-specific instrument calendar and session contract labels."""

from __future__ import annotations

from birzha.domain.market import AssetClass


def moex_calendar_id(*, engine: str, market: str, board: str) -> str:
    return f"MOEX:{engine.upper()}:{market.upper()}:{board.upper()}"


def moex_session_profile(asset_class: AssetClass) -> str:
    mapping = {
        "future": "MOEX_FORTS",
        "equity": "MOEX_EQUITIES",
        "index": "MOEX_INDICES",
        "fx": "MOEX_FX",
        "commodity": "MOEX_COMMODITIES",
    }
    return mapping.get(asset_class, "MOEX_GENERIC")



def moex_data_capabilities(asset_class: AssetClass) -> tuple[str, ...]:
    base = ("CANDLES", "TRADING_CALENDAR")
    if asset_class == "equity":
        return base + ("VOLUME", "TURNOVER", "TRADESTATS")
    if asset_class == "future":
        return base + (
            "VOLUME",
            "TURNOVER",
            "TRADESTATS",
            "OPEN_INTEREST",
            "FUTOI",
        )
    if asset_class == "index":
        return base
    if asset_class == "fx":
        return base + ("VOLUME", "TURNOVER")
    return base



def moex_roll_policy(asset_class: AssetClass) -> str:
    if asset_class == "future":
        return "MOEX_CAUSAL_LIQUIDITY_AS_OF_DATE"
    return "NOT_APPLICABLE"
