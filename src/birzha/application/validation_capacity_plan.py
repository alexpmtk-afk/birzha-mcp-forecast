"""Store-only planning of enlarged governed validation periods.

This module is deliberately pre-performance. It reads only a previously
verified D1 session calendar and its active-contract mapping, never candles,
features, forecasts, outcomes or model metrics. It therefore lets M23 determine
whether date enlargement can satisfy the frozen sample threshold without
spending the sealed holdout or re-downloading market history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from birzha.application.historical_data import HistoricalDataService
from birzha.application.validation_capacity import (
    ContractAwareCapacity,
    DEFAULT_HORIZONS,
    stored_contract_capacity,
)


@dataclass(frozen=True, slots=True)
class CapacityPeriod:
    from_date: str
    till_date: str
    capacity: ContractAwareCapacity
    minimum_observations: int

    @property
    def passes(self) -> bool:
        return all(
            count >= self.minimum_observations
            for count in self.capacity.non_overlapping_observations.values()
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "from_date": self.from_date,
            "till_date": self.till_date,
            "minimum_observations": self.minimum_observations,
            "passes": self.passes,
            **self.capacity.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ContiguousCapacityPlan:
    symbol: str
    status: str
    window_years: int | None
    development: CapacityPeriod | None
    holdout: CapacityPeriod | None
    available_from: str
    holdout_end: str
    minimum_observations: int
    evaluated_window_years: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "window_years": self.window_years,
            "available_from": self.available_from,
            "holdout_end": self.holdout_end,
            "minimum_observations": self.minimum_observations,
            "evaluated_window_years": list(self.evaluated_window_years),
            "development": None if self.development is None else self.development.to_dict(),
            "holdout": None if self.holdout is None else self.holdout.to_dict(),
            "model_status": "NOT_EVALUATED",
            "data_source": "VERIFIED_STORED_SESSION_CONTRACT_MAP_ONLY",
        }


def plan_contiguous_equal_windows(
    history: HistoricalDataService,
    symbol: str,
    *,
    available_from: str,
    holdout_end: str,
    minimum_observations: int = 20,
    minimum_window_years: int = 2,
    maximum_window_years: int = 12,
    step_sessions: int = 5,
    max_points: int = 400,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
) -> ContiguousCapacityPlan:
    """Return the shortest equal contiguous dev/holdout windows that can pass.

    The holdout end remains fixed. Candidate windows are full calendar years,
    contiguous and non-overlapping: for N years the holdout occupies the final
    N years ending at ``holdout_end`` and development occupies the N immediately
    preceding years. This prevents arbitrary cherry-picking of date ranges.

    The entire required range must already be D1/session verified. No sync or
    provider call is attempted here; missing prepared data fails closed.
    """
    symbol = symbol.strip()
    if not symbol:
        raise ValueError("symbol must be non-empty")
    if minimum_observations <= 0:
        raise ValueError("minimum_observations must be positive")
    if minimum_window_years <= 0 or maximum_window_years < minimum_window_years:
        raise ValueError("invalid window-year bounds")

    available_start = date.fromisoformat(available_from[:10])
    final_holdout_day = date.fromisoformat(holdout_end[:10])
    if final_holdout_day < available_start:
        raise ValueError("holdout_end must not be before available_from")
    if final_holdout_day.month != 12 or final_holdout_day.day != 31:
        raise ValueError("holdout_end must be a calendar year end")

    evaluated: list[int] = []
    last_dev: CapacityPeriod | None = None
    last_holdout: CapacityPeriod | None = None

    for years in range(minimum_window_years, maximum_window_years + 1):
        holdout_start = date(final_holdout_day.year - years + 1, 1, 1)
        development_end = date(holdout_start.year - 1, 12, 31)
        development_start = date(development_end.year - years + 1, 1, 1)
        if development_start < available_start:
            break

        required_from = development_start.isoformat()
        required_till = final_holdout_day.isoformat()
        if not history.is_range_verified(
            symbol,
            timeframe="D1",
            from_date=required_from,
            till_date=required_till,
        ):
            raise RuntimeError(
                "capacity planner requires one already-verified D1 range: "
                f"{symbol} {required_from}..{required_till}"
            )

        dev = CapacityPeriod(
            from_date=development_start.isoformat(),
            till_date=development_end.isoformat(),
            capacity=stored_contract_capacity(
                history,
                symbol,
                from_date=development_start.isoformat(),
                till_date=development_end.isoformat(),
                step_sessions=step_sessions,
                max_points=max_points,
                horizons=horizons,
            ),
            minimum_observations=minimum_observations,
        )
        holdout = CapacityPeriod(
            from_date=holdout_start.isoformat(),
            till_date=final_holdout_day.isoformat(),
            capacity=stored_contract_capacity(
                history,
                symbol,
                from_date=holdout_start.isoformat(),
                till_date=final_holdout_day.isoformat(),
                step_sessions=step_sessions,
                max_points=max_points,
                horizons=horizons,
            ),
            minimum_observations=minimum_observations,
        )
        evaluated.append(years)
        last_dev = dev
        last_holdout = holdout
        if dev.passes and holdout.passes:
            return ContiguousCapacityPlan(
                symbol=symbol,
                status="FEASIBLE",
                window_years=years,
                development=dev,
                holdout=holdout,
                available_from=available_start.isoformat(),
                holdout_end=final_holdout_day.isoformat(),
                minimum_observations=minimum_observations,
                evaluated_window_years=tuple(evaluated),
            )

    return ContiguousCapacityPlan(
        symbol=symbol,
        status="INSUFFICIENT_STORED_RANGE_OR_CAPACITY",
        window_years=None,
        development=last_dev,
        holdout=last_holdout,
        available_from=available_start.isoformat(),
        holdout_end=final_holdout_day.isoformat(),
        minimum_observations=minimum_observations,
        evaluated_window_years=tuple(evaluated),
    )
