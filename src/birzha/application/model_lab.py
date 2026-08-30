"""Statistical acceptance layer for causal walk-forward Forecast validation."""

from __future__ import annotations

import math
from dataclasses import dataclass

from birzha.application.validation import WalkForwardValidator
from birzha.domain.validation import (
    HorizonAcceptance,
    ModelAcceptanceReport,
    WalkForwardReport,
)


@dataclass(slots=True)
class ModelAcceptanceService:
    validator: WalkForwardValidator
    minimum_observations: int = 20
    minimum_directional_coverage: float = 0.70
    minimum_wilson_lower_95: float = 0.50

    def assess(
        self,
        symbol: str,
        *,
        start_date: str,
        end_date: str,
        step_sessions: int = 5,
        max_points: int = 60,
    ) -> ModelAcceptanceReport:
        report = self.validator.run(
            symbol,
            start_date=start_date,
            end_date=end_date,
            step_sessions=step_sessions,
            max_points=max_points,
        )
        return assess_walk_forward(
            report,
            minimum_observations=self.minimum_observations,
            minimum_directional_coverage=self.minimum_directional_coverage,
            minimum_wilson_lower_95=self.minimum_wilson_lower_95,
        )


def assess_walk_forward(
    report: WalkForwardReport,
    *,
    minimum_observations: int = 20,
    minimum_directional_coverage: float = 0.70,
    minimum_wilson_lower_95: float = 0.50,
) -> ModelAcceptanceReport:
    if minimum_observations <= 0:
        raise ValueError("minimum_observations must be > 0")
    if not 0 < minimum_directional_coverage <= 1:
        raise ValueError("minimum_directional_coverage must be in (0, 1]")
    if not 0 <= minimum_wilson_lower_95 <= 1:
        raise ValueError("minimum_wilson_lower_95 must be in [0, 1]")

    horizons: list[HorizonAcceptance] = []
    for metric in report.metrics:
        reasons: list[str] = []
        coverage = (
            metric.directional_observations / metric.observations
            if metric.observations > 0
            else None
        )
        wilson = _wilson_lower_bound(
            metric.direction_hits,
            metric.directional_observations,
        )

        if metric.observations < minimum_observations:
            reasons.append(
                f"insufficient_observations={metric.observations}<{minimum_observations}"
            )
        if coverage is None or coverage < minimum_directional_coverage:
            reasons.append(
                "directional_coverage_below_threshold="
                f"{0.0 if coverage is None else coverage:.6f}<{minimum_directional_coverage:.6f}"
            )
        if wilson is None or wilson <= minimum_wilson_lower_95:
            reasons.append(
                "wilson_lower_95_not_above_random="
                f"{0.0 if wilson is None else wilson:.6f}<={minimum_wilson_lower_95:.6f}"
            )

        if metric.observations < minimum_observations:
            status = "INSUFFICIENT_SAMPLE"
        elif reasons:
            status = "REJECTED"
        else:
            status = "ACCEPTED"

        horizons.append(
            HorizonAcceptance(
                sessions=metric.sessions,
                observations=metric.observations,
                directional_observations=metric.directional_observations,
                directional_coverage=round(coverage, 6) if coverage is not None else None,
                direction_hit_rate=metric.direction_hit_rate,
                wilson_lower_95=round(wilson, 6) if wilson is not None else None,
                minimum_observations=minimum_observations,
                required_directional_coverage=minimum_directional_coverage,
                required_wilson_lower=minimum_wilson_lower_95,
                status=status,
                reasons=tuple(reasons),
            )
        )

    if not horizons or report.status in {"FAILED", "NO_ELIGIBLE_POINTS"}:
        overall = "FAILED"
    elif any(item.status == "INSUFFICIENT_SAMPLE" for item in horizons):
        overall = "INSUFFICIENT_SAMPLE"
    elif all(item.status == "ACCEPTED" for item in horizons):
        overall = "ACCEPTED"
    else:
        overall = "REJECTED"

    return ModelAcceptanceReport(
        symbol=report.symbol,
        start_date=report.start_date,
        end_date=report.end_date,
        engine_versions=report.engine_versions,
        walk_forward_status=report.status,
        completed_forecasts=report.completed_forecasts,
        failed_forecasts=report.failed_forecasts,
        horizons=tuple(horizons),
        status=overall,
    )


def _wilson_lower_bound(successes: int, observations: int, *, z: float = 1.959963984540054) -> float | None:
    if observations <= 0:
        return None
    if successes < 0 or successes > observations:
        raise ValueError("successes must be between 0 and observations")
    p = successes / observations
    z2 = z * z
    denominator = 1.0 + z2 / observations
    centre = p + z2 / (2.0 * observations)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * observations)) / observations)
    return (centre - margin) / denominator
