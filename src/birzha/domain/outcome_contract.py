"""Formal first-touch Outcome Contract for Protocol 08."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


OUTCOME_CONTRACT_VERSION = "OUTCOME_CONTRACT_V1_FIRST_TOUCH"
OutcomeClass = Literal["UP_FIRST", "DOWN_FIRST", "NEITHER", "AMBIGUOUS"]


@dataclass(frozen=True, slots=True)
class HorizonOutcomeContract:
    horizon_sessions: int
    status: str
    outcome: OutcomeClass | None
    target_session_end: str | None
    observation_end: str | None
    first_hit_at: str | None
    first_hit_window_start: str | None
    first_hit_window_end: str | None
    hit_time_resolution: str | None
    hit_session_index: int | None
    time_to_hit_minutes: float | None
    max_up_excursion_pct: float | None
    max_down_excursion_pct: float | None
    mfe_pct: float | None
    mae_pct: float | None
    ambiguity_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutcomeContract:
    outcome_contract_id: str
    version: str
    prediction_contract_id: str
    prediction_version: str
    symbol: str
    secid: str
    t0: str
    evaluated_through: str
    p0: float
    up_barrier: float
    down_barrier: float
    available_future_sessions: int
    horizons: tuple[HorizonOutcomeContract, ...]
    status: str
    excursion_convention: str = (
        "direction-neutral: MFE=max upward excursion from P0; "
        "MAE=max downward excursion magnitude from P0"
    )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["horizons"] = [item.to_dict() for item in self.horizons]
        return payload
