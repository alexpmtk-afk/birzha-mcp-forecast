"""Explainable ex-ante forecast domain contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal
import math
from birzha.domain.price_levels import PriceLevelEvidence, LEVEL_EVIDENCE_VERSION, validate_level_evidence


Direction = Literal["UP", "DOWN", "NEUTRAL"]
FORECAST_RECORD_CONTRACT_VERSION = "FORECAST_RECORD_V1_PROTOCOL_08"
BASELINE_EVIDENCE_RECORD_VERSION = "FORECAST_RECORD_V2_BASELINE_EVIDENCE"
LEVEL_EVIDENCE_RECORD_VERSION = "FORECAST_RECORD_V3_LEVEL_EVIDENCE"
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
    decision_status: str | None = None
    abstention_reasons: tuple[str, ...] = ()
    level_evidence: tuple[PriceLevelEvidence, ...] = ()

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
        payload = {
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
        if self.record_version in {BASELINE_EVIDENCE_RECORD_VERSION, LEVEL_EVIDENCE_RECORD_VERSION}:
            availability.update({
                'control': 'UNAVAILABLE' if self.control == 'UNKNOWN' else 'AVAILABLE',
                'route': 'UNAVAILABLE' if self.route == 'UNAVAILABLE' else 'AVAILABLE',
                'scenario': 'UNAVAILABLE' if self.primary_scenario is None else 'AVAILABLE',
                'probability': 'UNAVAILABLE',
                'control_confidence': 'UNAVAILABLE',
                'route_confidence': 'UNAVAILABLE',
                'directional_estimate': 'UNAVAILABLE' if self.decision_status == 'ABSTAIN' else 'AVAILABLE',
            })
            payload['decision_status'] = self.decision_status
            payload['abstention_reasons'] = list(self.abstention_reasons)
            if self.record_version == LEVEL_EVIDENCE_RECORD_VERSION:
                payload["level_evidence_version"] = LEVEL_EVIDENCE_VERSION
                payload["level_evidence"] = [item.to_dict() for item in self.level_evidence]
            validate_baseline_evidence_payload(payload)
        return payload


def _availability(value: object | None) -> str:
    return "AVAILABLE" if value is not None else "UNAVAILABLE"


def validate_baseline_evidence_payload(data: dict[str, object]) -> None:
    """Validate V2/V3 semantics equally for domain records and capture dictionaries."""
    if data.get('record_version') not in {BASELINE_EVIDENCE_RECORD_VERSION, LEVEL_EVIDENCE_RECORD_VERSION}:
        raise ValueError('not a baseline evidence contract')
    decision = data.get('decision_status')
    if decision not in {'ABSTAIN', 'BASELINE_NEUTRAL', 'BASELINE_DIRECTIONAL_ESTIMATE'}:
        raise ValueError('baseline evidence record requires an explicit decision status')
    reasons = data.get('abstention_reasons')
    if not isinstance(reasons, list) or any(not isinstance(r, str) or not r for r in reasons):
        raise ValueError('baseline evidence requires a list of explicit reasons')
    if (decision == 'ABSTAIN') != bool(reasons):
        raise ValueError('abstention status and reasons must agree')
    if data.get('control') != 'UNKNOWN' or data.get('route') != 'UNAVAILABLE':
        raise ValueError('baseline evidence does not establish Control or Route')
    absent = ('primary_scenario', 'alternative_scenario', 'confirmation_level', 'invalidation_level', 'stop_level', 'reversal_condition', 'market_state', 'location', 'pressure', 'alignment', 'control_confidence', 'route_confidence', 'probability')
    if any(data.get(name) is not None for name in absent) or data.get('entry_levels') or data.get('target_levels'):
        raise ValueError('full scenario and execution plan are not implemented in baseline evidence')
    scenario = data.get('scenario')
    execution = data.get('execution_plan')
    if not isinstance(scenario, dict) or any(scenario.get(name) is not None for name in ('primary', 'alternative', 'confirmation_level', 'invalidation_level', 'reversal_condition')):
        raise ValueError('nested scenario contradicts unavailable full-engine evidence')
    if not isinstance(execution, dict) or execution.get('entries') or execution.get('targets') or execution.get('stop') is not None:
        raise ValueError('nested execution plan contradicts unavailable full-engine evidence')
    if data.get('validation_status') != 'UNVALIDATED_BASELINE':
        raise ValueError('baseline evidence does not establish predictive quality')
    direction = data.get('direction')
    if direction not in {'UP', 'DOWN', 'NEUTRAL'}:
        raise ValueError('invalid baseline direction')
    if (decision == 'BASELINE_DIRECTIONAL_ESTIMATE') != (direction in {'UP', 'DOWN'}):
        raise ValueError('baseline decision and direction must agree')
    strength = data.get('signal_strength')
    if type(strength) not in (int, float) or not 0 <= strength <= 1 or not math.isfinite(strength):
        raise ValueError('invalid baseline signal strength')
    horizons = data.get('horizons')
    if not isinstance(horizons, list) or [h.get('sessions') for h in horizons if isinstance(h, dict)] != [5, 10, 20]:
        raise ValueError('baseline evidence requires ordered 5/10/20 horizons')
    if any(h.get('direction') != direction or h.get('signal_strength') != strength for h in horizons):
        raise ValueError('horizon estimates contradict the baseline decision')
    if decision == 'ABSTAIN':
        if strength != 0 or any(h.get('direction') != 'NEUTRAL' or h.get('signal_strength') != 0 or h.get('expected_move_pct') is not None or h.get('adverse_move_pct') is not None for h in horizons):
            raise ValueError('abstention cannot contain a directional estimate')
    availability = data.get('field_availability')
    if not isinstance(availability, dict) or any(availability.get(name) != 'UNAVAILABLE' for name in ('control', 'route', 'scenario', 'probability', 'control_confidence', 'route_confidence')):
        raise ValueError('missing explicit unavailable full-engine evidence')
    if availability.get('directional_estimate') != ('UNAVAILABLE' if decision == 'ABSTAIN' else 'AVAILABLE'):
        raise ValueError('directional availability contradicts decision')

    if data.get("record_version") == LEVEL_EVIDENCE_RECORD_VERSION:
        validate_level_evidence(data)
