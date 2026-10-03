"""Explainable ex-ante forecast domain contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


Direction = Literal["UP", "DOWN", "NEUTRAL"]
FORECAST_RECORD_CONTRACT_VERSION = "FORECAST_RECORD_V1_PROTOCOL_08"
LEGACY_FORECAST_RECORD_VERSION = "FORECAST_RECORD_LEGACY_V0"


@dataclass(frozen=True, slots=True)
class HorizonForecast:
    sessions: int
    direction: Direction
    signal_strength: float
    expected_move_pct: float | None
    adverse_move_pct: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ForecastRecord:
    forecast_id: str
    symbol: str
    secid: str
    created_at_t0: str
    engine_version: str
    direction: Direction
    signal_strength: float
    control: str
    route: str
    horizons: tuple[HorizonForecast, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    validation_status: str
    reference_price: float | None = None
    primary_scenario: str | None = None
    alternative_scenario: str | None = None
    confirmation_level: float | None = None
    invalidation_level: float | None = None
    key_levels: tuple[float, ...] = ()
    record_version: str = FORECAST_RECORD_CONTRACT_VERSION
    snapshot_id: str | None = None
    snapshot_contract_version: str | None = None
    prediction_contract_id: str | None = None
    prediction_contract_version: str | None = None
    market_state: str | None = None
    location: str | None = None
    pressure: str | None = None
    alignment: str | None = None
    entry_levels: tuple[float, ...] = ()
    stop_level: float | None = None
    target_levels: tuple[float, ...] = ()
    reversal_condition: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the immutable Protocol-08 journal payload.

        Stage-F owns the durable shape, not the later engines. Fields whose
        producing engine does not exist yet remain explicit None / empty
        collections; they are never synthesized from unrelated signals.
        """
        availability = {
            "market_state": _availability(self.market_state),
            "location": _availability(self.location),
            "pressure": _availability(self.pressure),
            "alignment": _availability(self.alignment),
            "entries": "AVAILABLE" if self.entry_levels else "UNAVAILABLE",
            "stop": _availability(self.stop_level),
            "targets": "AVAILABLE" if self.target_levels else "UNAVAILABLE",
            "reversal": _availability(self.reversal_condition),
        }
        return {
            "record_version": self.record_version,
            "forecast_id": self.forecast_id,
            "as_of": self.created_at_t0,
            "symbol": self.symbol,
            "secid": self.secid,
            "instrument": {
                "symbol": self.symbol,
                "contract_id": self.secid,
            },
            "snapshot_id": self.snapshot_id,
            "snapshot_contract_version": self.snapshot_contract_version,
            "prediction_contract_id": self.prediction_contract_id,
            "prediction_contract_version": self.prediction_contract_version,
            "created_at_t0": self.created_at_t0,
            "engine_version": self.engine_version,
            "direction": self.direction,
            "signal_strength": self.signal_strength,
            "market_state": self.market_state,
            "location": self.location,
            "pressure": self.pressure,
            "control": self.control,
            "route": self.route,
            "alignment": self.alignment,
            "horizons": [h.to_dict() for h in self.horizons],
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "validation_status": self.validation_status,
            "reference_price": self.reference_price,
            "primary_scenario": self.primary_scenario,
            "alternative_scenario": self.alternative_scenario,
            "confirmation_level": self.confirmation_level,
            "invalidation_level": self.invalidation_level,
            "key_levels": list(self.key_levels),
            "entry_levels": list(self.entry_levels),
            "stop_level": self.stop_level,
            "target_levels": list(self.target_levels),
            "reversal_condition": self.reversal_condition,
            "scenario": {
                "primary": self.primary_scenario,
                "alternative": self.alternative_scenario,
                "confirmation_level": self.confirmation_level,
                "invalidation_level": self.invalidation_level,
                "reversal_condition": self.reversal_condition,
            },
            "execution_plan": {
                "entries": list(self.entry_levels),
                "stop": self.stop_level,
                "targets": list(self.target_levels),
            },
            "versions": {
                "forecast_record": self.record_version,
                "engine": self.engine_version,
                "snapshot": self.snapshot_contract_version,
                "prediction_contract": self.prediction_contract_version,
            },
            "field_availability": availability,
        }


def _availability(value: object | None) -> str:
    return "AVAILABLE" if value is not None else "UNAVAILABLE"
