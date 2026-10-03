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
                STATUS_PARTIAL if supports_flow else STATUS_NOT_APPLICABLE,
                "Trade count is implemented from TradeStats/public-trade trades_b+trades_s when those fields are available; otherwise it remains NULL as required by Protocol 08."
                if supports_flow
                else "Trade count is not claimed for this instrument type.",
            ),
            "open_interest": self._item(
                STATUS_PARTIAL if is_future else STATUS_NOT_APPLICABLE,
                "FUTOI/OI pipeline exists, but availability/coverage can be delayed or missing."
                if is_future
                else "Open interest is not applicable to this instrument type.",
            ),
            "delta": self._item(
                STATUS_PARTIAL if supports_flow else STATUS_NOT_APPLICABLE,
                "TradeStats/public-trade Delta pipeline exists, but historical coverage is not guaranteed."
                if supports_flow
                else "Delta flow is not claimed for this instrument type.",
            ),
            "cumulative_delta": self._item(
                STATUS_PARTIAL if supports_flow else STATUS_NOT_APPLICABLE,
                "Latest-session Cumulative Delta is calculated on demand from causal TradeStats/public-trade rows; availability follows the underlying Delta coverage."
                if supports_flow
                else "Cumulative Delta is not applicable without Delta flow.",
            ),
            "atr": self._item(
                STATUS_READY,
                "ATR-based volatility feature is implemented for candle timeframes.",
            ),
            "session_vwap": self._item(
                STATUS_MISSING,
                "Current vwap_20 is a rolling candle VWAP, not the session VWAP required by Protocol 08.",
            ),
            "volume_profile": self._item(
                STATUS_PARTIAL,
                "POC/VAL/VAH/HVN/LVN exist, but the current snapshot uses an approximate candle-price/volume proxy.",
            ),
            "normalized_features": self._item(
                STATUS_PARTIAL,
                "ATR%, volume ratio and Delta ratio exist; the full normalized feature layer is not complete.",
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
        return {
            "schema": "FORECAST_INPUT_READINESS_V1",
            "scope": "IMPLEMENTATION_READINESS",
            "symbol": symbol,
            "instrument": instrument.to_dict(),
            "items": items,
            "blocking_items": blocking,
            "ready_for_protocol_08": not blocking,
        }

    @staticmethod
    def _item(status: str, note: str) -> dict[str, str]:
        return {"status": status, "note": note}
