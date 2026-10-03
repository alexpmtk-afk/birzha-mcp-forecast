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
