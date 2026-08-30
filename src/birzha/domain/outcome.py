"""Append-only forecast outcome contracts.

Outcomes are derived only after future exchange sessions exist. They never
modify the immutable Forecast Record; each horizon is written as a separate
append-only observation tied to one forecast_id.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class HorizonOutcome:
    outcome_id: str
    forecast_id: str
    symbol: str
    secid: str
    horizon_sessions: int
    reference_price: float
    target_session_end: str
    target_close: float
    actual_return_pct: float
    direction_hit: bool | None
    max_favorable_excursion_pct: float | None
    max_adverse_excursion_pct: float | None
    status: str = "OBSERVED"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutcomeEvaluation:
    forecast_id: str
    symbol: str
    secid: str
    available_future_sessions: int
    outcomes: tuple[HorizonOutcome, ...]
    pending_horizons: tuple[int, ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "forecast_id": self.forecast_id,
            "symbol": self.symbol,
            "secid": self.secid,
            "available_future_sessions": self.available_future_sessions,
            "outcomes": [item.to_dict() for item in self.outcomes],
            "pending_horizons": list(self.pending_horizons),
            "status": self.status,
        }
