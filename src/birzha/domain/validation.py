"""Historical walk-forward validation and model-acceptance contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class HorizonValidationMetrics:
    sessions: int
    observations: int
    directional_observations: int
    direction_hits: int
    direction_hit_rate: float | None
    mean_actual_return_pct: float | None
    mean_absolute_error_pct: float | None
    mean_signed_error_pct: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WalkForwardReport:
    symbol: str
    start_date: str
    end_date: str
    requested_points: int
    completed_forecasts: int
    failed_forecasts: int
    engine_versions: tuple[str, ...]
    metrics: tuple[HorizonValidationMetrics, ...]
    failures: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "requested_points": self.requested_points,
            "completed_forecasts": self.completed_forecasts,
            "failed_forecasts": self.failed_forecasts,
            "engine_versions": list(self.engine_versions),
            "metrics": [item.to_dict() for item in self.metrics],
            "failures": list(self.failures),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class HorizonAcceptance:
    sessions: int
    observations: int
    directional_observations: int
    directional_coverage: float | None
    direction_hit_rate: float | None
    wilson_lower_95: float | None
    minimum_observations: int
    required_directional_coverage: float
    required_wilson_lower: float
    status: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


@dataclass(frozen=True, slots=True)
class ModelAcceptanceReport:
    symbol: str
    start_date: str
    end_date: str
    engine_versions: tuple[str, ...]
    walk_forward_status: str
    completed_forecasts: int
    failed_forecasts: int
    failures: tuple[str, ...]
    horizons: tuple[HorizonAcceptance, ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "engine_versions": list(self.engine_versions),
            "walk_forward_status": self.walk_forward_status,
            "completed_forecasts": self.completed_forecasts,
            "failed_forecasts": self.failed_forecasts,
            "failures": list(self.failures),
            "horizons": [item.to_dict() for item in self.horizons],
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class DevelopmentHoldoutReport:
    symbol: str
    development_start: str
    development_end: str
    holdout_start: str
    holdout_end: str
    development: ModelAcceptanceReport
    holdout: ModelAcceptanceReport | None
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "development_start": self.development_start,
            "development_end": self.development_end,
            "holdout_start": self.holdout_start,
            "holdout_end": self.holdout_end,
            "development": self.development.to_dict(),
            "holdout": self.holdout.to_dict() if self.holdout is not None else None,
            "holdout_evaluated": self.holdout is not None,
            "status": self.status,
        }
