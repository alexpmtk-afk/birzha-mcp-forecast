"""Forecast Protocol v1 input-readiness audit.

This reports implementation readiness only. It does not claim that every
historical date has complete stored coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

from birzha.application.market_data import MarketDataService


STATUS_READY = "READY"
STATUS_PARTIAL = "PARTIAL"
STATUS_MISSING = "MISSING"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(slots=True)
class ForecastInputReadinessService:
    market_data: MarketDataService

    def audit(self, symbol: str) -> dict[str, object]:
        instrument = self.market_data.resolve(symbol)
        capabilities = set(instrument.data_capabilities)
        is_future = instrument.asset_class == "future"
        is_index = instrument.asset_class == "index"
        supports_flow = "TRADESTATS" in capabilities

        items = {
            "instrument_contract": self._item(
                STATUS_READY,
                "Instrument metadata/calendar/session/capabilities/roll policy are implemented.",
            ),
            "ohlcv_d1_h1_m15": self._item(
                STATUS_READY,
                "D1/H1/M15 OHLCV pipeline is implemented.",
            ),
            "volume": self._item(
                STATUS_NOT_APPLICABLE if is_index else STATUS_READY,
                "Candle volume is available where the market provides it."
                if not is_index
                else "Index volume is not treated as a required flow input.",
            ),
            "turnover": self._item(
                STATUS_READY if "TURNOVER" in capabilities else STATUS_NOT_APPLICABLE,
                "MOEX candle value is the implemented turnover source."
                if "TURNOVER" in capabilities
                else "Turnover is not claimed for this instrument type.",
            ),
            "number_of_trades": self._item(
                STATUS_READY if supports_flow else STATUS_NOT_APPLICABLE,
                "Trade count calculation is implemented. At a specific T0 it may be NULL when the source does not provide a real count."
                if supports_flow
                else "Trade count is not claimed for this instrument type.",
            ),
            "open_interest": self._item(
                STATUS_READY if is_future else STATUS_NOT_APPLICABLE,
                "OI/FUTOI handling is implemented with causal fail-closed semantics. A specific T0 may still have OI unavailable when publication timing is not provable."
                if is_future
                else "Open interest is not applicable to this instrument type.",
            ),
            "delta": self._item(
                STATUS_READY if supports_flow else STATUS_NOT_APPLICABLE,
                "TradeStats/public-trade Delta calculation is implemented; per-T0 availability is reported by snapshot quality/coverage."
                if supports_flow
                else "Delta flow is not claimed for this instrument type.",
            ),
            "cumulative_delta": self._item(
                STATUS_READY if supports_flow else STATUS_NOT_APPLICABLE,
                "Latest-session Cumulative Delta is implemented on demand from causal TradeStats/public-trade rows."
                if supports_flow
                else "Cumulative Delta is not applicable without Delta flow.",
            ),
            "atr": self._item(
                STATUS_READY,
                "ATR-based volatility feature is implemented for candle timeframes.",
            ),
            "session_vwap": self._item(
                STATUS_READY if supports_flow else STATUS_NOT_APPLICABLE,
                "Session VWAP is implemented on demand from causal raw public trades or an explicit TradeStats VWAP; it is never replaced by rolling vwap_20."
                if supports_flow
                else "Session VWAP is not claimed when the instrument has no applicable traded-volume flow.",
            ),
            "volume_profile": self._item(
                STATUS_NOT_APPLICABLE if is_index else STATUS_READY,
                "Exact POC/VAL/VAH/HVN/LVN is implemented from causal raw public trades; when exact trade coverage is absent the snapshot may expose an explicitly labeled approximate fallback."
                if not is_index
                else "Trade-volume profile is not claimed for an index without traded-volume flow.",
            ),
            "normalized_features": self._item(
                STATUS_READY,
                "Minimal Protocol 08 normalized layer is implemented: ATR-scaled returns/distances, price location, relative volume, Delta/Volume and OI change ratio with honest NULL semantics.",
            ),
            "quality_status": self._item(
                STATUS_READY,
                "MarketSnapshot data-quality contract is implemented.",
            ),
        }

        blocking = [
            key
            for key, value in items.items()
            if value["status"] in {STATUS_MISSING, STATUS_PARTIAL}
        ]
        coverage_caveats: list[str] = []
        if supports_flow:
            coverage_caveats.append(
                "Trade-derived inputs can be UNAVAILABLE at a specific T0 when no causal stored flow exists."
            )
        if is_future:
            coverage_caveats.append(
                "FUTOI/OI can be UNAVAILABLE at a specific T0 when publication time is delayed or not provable."
            )

        return {
            "schema": "FORECAST_INPUT_READINESS_V2",
            "scope": "IMPLEMENTATION_READINESS",
            "symbol": symbol,
            "instrument": instrument.to_dict(),
            "items": items,
            "blocking_items": blocking,
            "coverage_caveats": coverage_caveats,
            "ready_for_protocol_08": not blocking,
        }

    @staticmethod
    def _item(status: str, note: str) -> dict[str, str]:
        return {"status": status, "note": note}
