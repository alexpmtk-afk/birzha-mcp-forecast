"""Development-only model calibration with untouched holdout evaluation."""

from __future__ import annotations

from dataclasses import dataclass

from birzha.application.forecast import ForecastParameters, ForecastService
from birzha.application.model_lab import ModelAcceptanceService, holdout_start_date
from birzha.application.validation import WalkForwardValidator
from birzha.domain.validation import ModelAcceptanceReport


DEFAULT_CANDIDATES = (
    ForecastParameters(name="baseline"),
    ForecastParameters(name="lower_threshold", direction_threshold=0.75),
    ForecastParameters(name="trend_heavy", d1_weight=0.65, h1_weight=0.25, m15_weight=0.10, direction_threshold=0.85, flow_weight=0.80, profile_weight=0.80),
    ForecastParameters(name="balanced_features", d1_weight=0.50, h1_weight=0.30, m15_weight=0.20, direction_threshold=0.80, flow_weight=1.15, profile_weight=1.15),
    ForecastParameters(name="strict", direction_threshold=1.20),
)


@dataclass(frozen=True, slots=True)
class CalibrationCandidateResult:
    parameters: ForecastParameters
    objective: float
    development: ModelAcceptanceReport

    def to_dict(self) -> dict[str, object]:
        return {"parameters": self.parameters.to_dict(), "objective": self.objective, "development": self.development.to_dict()}


@dataclass(frozen=True, slots=True)
class ModelCalibrationReport:
    symbol: str
    development_start: str
    split_date: str
    holdout_end: str
    selected: ForecastParameters
    candidates: tuple[CalibrationCandidateResult, ...]
    holdout: ModelAcceptanceReport
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "development_start": self.development_start,
            "split_date": self.split_date,
            "holdout_end": self.holdout_end,
            "selected": self.selected.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "holdout": self.holdout.to_dict(),
            "status": self.status,
        }


@dataclass(slots=True)
class ModelCalibrationService:
    validator: WalkForwardValidator
    minimum_observations: int = 20
    minimum_directional_coverage: float = 0.70
    minimum_wilson_lower_95: float = 0.50

    def calibrate(
        self,
        symbol: str,
        *,
        development_start: str,
        split_date: str,
        holdout_end: str,
        step_sessions: int = 5,
        max_points: int = 60,
        candidates: tuple[ForecastParameters, ...] = DEFAULT_CANDIDATES,
    ) -> ModelCalibrationReport:
        if not development_start < split_date < holdout_end:
            raise ValueError("expected development_start < split_date < holdout_end")
        holdout_start = holdout_start_date(split_date)
        if holdout_start >= holdout_end:
            raise ValueError("holdout period must contain dates after split_date")
        if not candidates:
            raise ValueError("at least one calibration candidate is required")
        results = []
        for parameters in candidates:
            report = self._acceptance_for(parameters).assess(
                symbol,
                start_date=development_start,
                end_date=split_date,
                step_sessions=step_sessions,
                max_points=max_points,
            )
            results.append(CalibrationCandidateResult(parameters, _objective(report), report))
        ranked = tuple(sorted(results, key=lambda item: (item.objective, item.parameters.name), reverse=True))
        selected = ranked[0].parameters
        holdout = self._acceptance_for(selected).assess(
            symbol,
            start_date=holdout_start,
            end_date=holdout_end,
            step_sessions=step_sessions,
            max_points=max_points,
        )
        development = ranked[0].development
        if development.status == "ACCEPTED" and holdout.status == "ACCEPTED":
            status = "ACCEPTED"
        elif holdout.status in {"FAILED", "INSUFFICIENT_SAMPLE"}:
            status = holdout.status
        else:
            status = "REJECTED"
        return ModelCalibrationReport(
            symbol=symbol,
            development_start=development_start,
            split_date=split_date,
            holdout_end=holdout_end,
            selected=selected,
            candidates=ranked,
            holdout=holdout,
            status=status,
        )

    def _acceptance_for(self, parameters: ForecastParameters) -> ModelAcceptanceService:
        forecast = ForecastService(self.validator.forecasts.snapshots, parameters=parameters)
        validator = WalkForwardValidator(
            market_data=self.validator.market_data,
            forecasts=forecast,
            calendar=self.validator.calendar,
            history=self.validator.history,
            historical_flow=self.validator.historical_flow,
        )
        return ModelAcceptanceService(
            validator=validator,
            minimum_observations=self.minimum_observations,
            minimum_directional_coverage=self.minimum_directional_coverage,
            minimum_wilson_lower_95=self.minimum_wilson_lower_95,
        )


def _objective(report: ModelAcceptanceReport) -> float:
    if not report.horizons:
        return -1.0
    scores = []
    for item in report.horizons:
        hit = item.direction_hit_rate or 0.0
        coverage = item.directional_coverage or 0.0
        wilson = item.wilson_lower_95 or 0.0
        sample_factor = min(1.0, item.observations / max(1, item.minimum_observations))
        scores.append(sample_factor * (0.50 * hit + 0.30 * wilson + 0.20 * coverage))
    return round(sum(scores) / len(scores), 8)


@dataclass(frozen=True, slots=True)
class MultiSymbolCandidateResult:
    parameters: ForecastParameters
    objective: float
    development: tuple[ModelAcceptanceReport, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "parameters": self.parameters.to_dict(),
            "objective": self.objective,
            "development": [item.to_dict() for item in self.development],
        }


@dataclass(frozen=True, slots=True)
class MultiSymbolCalibrationReport:
    symbols: tuple[str, ...]
    development_start: str
    split_date: str
    holdout_end: str
    selected: ForecastParameters
    candidates: tuple[MultiSymbolCandidateResult, ...]
    holdout: tuple[ModelAcceptanceReport, ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "symbols": list(self.symbols),
            "development_start": self.development_start,
            "split_date": self.split_date,
            "holdout_end": self.holdout_end,
            "selected": self.selected.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "holdout": [item.to_dict() for item in self.holdout],
            "status": self.status,
        }


def calibrate_across_symbols(
    service: ModelCalibrationService,
    symbols: tuple[str, ...],
    *,
    development_start: str,
    split_date: str,
    holdout_end: str,
    step_sessions: int = 5,
    max_points: int = 60,
    candidates: tuple[ForecastParameters, ...] = DEFAULT_CANDIDATES,
) -> MultiSymbolCalibrationReport:
    if not symbols:
        raise ValueError("at least one symbol is required")
    if not development_start < split_date < holdout_end:
        raise ValueError("expected development_start < split_date < holdout_end")
    holdout_start = holdout_start_date(split_date)
    if holdout_start >= holdout_end:
        raise ValueError("holdout period must contain dates after split_date")
    if not candidates:
        raise ValueError("at least one calibration candidate is required")

    ranked_items: list[MultiSymbolCandidateResult] = []
    for parameters in candidates:
        assessor = service._acceptance_for(parameters)
        development = tuple(
            assessor.assess(
                symbol,
                start_date=development_start,
                end_date=split_date,
                step_sessions=step_sessions,
                max_points=max_points,
            )
            for symbol in symbols
        )
        objective = round(sum(_objective(item) for item in development) / len(development), 8)
        ranked_items.append(MultiSymbolCandidateResult(parameters, objective, development))

    ranked = tuple(sorted(ranked_items, key=lambda item: (item.objective, item.parameters.name), reverse=True))
    selected = ranked[0].parameters
    holdout_assessor = service._acceptance_for(selected)
    holdout = tuple(
        holdout_assessor.assess(
            symbol,
            start_date=holdout_start,
            end_date=holdout_end,
            step_sessions=step_sessions,
            max_points=max_points,
        )
        for symbol in symbols
    )
    development_ok = all(item.status == "ACCEPTED" for item in ranked[0].development)
    holdout_ok = all(item.status == "ACCEPTED" for item in holdout)
    if development_ok and holdout_ok:
        status = "ACCEPTED"
    elif any(item.status in {"FAILED", "INSUFFICIENT_SAMPLE"} for item in holdout):
        status = "INSUFFICIENT_OR_FAILED"
    else:
        status = "REJECTED"
    return MultiSymbolCalibrationReport(
        symbols=symbols,
        development_start=development_start,
        split_date=split_date,
        holdout_end=holdout_end,
        selected=selected,
        candidates=ranked,
        holdout=holdout,
        status=status,
    )
