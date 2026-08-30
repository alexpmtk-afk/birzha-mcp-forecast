"""Explainable ex-ante forecast domain contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


Direction = Literal["UP", "DOWN", "NEUTRAL"]


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

    def to_dict(self) -> dict[str, object]:
        return {
            "forecast_id": self.forecast_id,
            "symbol": self.symbol,
            "secid": self.secid,
            "created_at_t0": self.created_at_t0,
            "engine_version": self.engine_version,
            "direction": self.direction,
            "signal_strength": self.signal_strength,
            "control": self.control,
            "route": self.route,
            "horizons": [h.to_dict() for h in self.horizons],
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "validation_status": self.validation_status,
        }
