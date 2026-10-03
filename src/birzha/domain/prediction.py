"""Formal Prediction Contract for Protocol 08 / MASTER ROADMAP Stage D."""

from __future__ import annotations

from dataclasses import asdict, dataclass


PREDICTION_CONTRACT_VERSION = "PREDICTION_CONTRACT_V1_ATR14_K1"


@dataclass(frozen=True, slots=True)
class PredictionContract:
    contract_id: str
    version: str
    symbol: str
    secid: str
    t0: str
    p0: float
    price_coordinate: str
    significant_move_definition: str
    volatility_measure: str
    causal_volatility_price: float
    barrier_k: float
    up_barrier: float
    down_barrier: float
    horizons_sessions: tuple[int, ...]
    up_hit_rule: str
    down_hit_rule: str
    ambiguous_path_policy: str
    rollover_policy: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["horizons_sessions"] = list(self.horizons_sessions)
        return payload
