from __future__ import annotations

from datetime import date, timedelta

import pytest

from birzha.application.validation_capacity_plan import plan_contiguous_equal_windows


class FakeStore:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self.rows = rows

    def stored_session_contracts(self, symbol: str, from_date: str, till_date: str):
        return tuple(
            (day, secid)
            for day, secid in self.rows
            if from_date[:10] <= day <= till_date[:10]
        )


class FakeHistory:
    def __init__(self, *, verified: bool = True) -> None:
        rows: list[tuple[str, str]] = []
        for year in range(2019, 2025):
            start = date(year, 1, 1)
            secid = f"BR{year}"
            for offset in range(30):
                rows.append(((start + timedelta(days=offset)).isoformat(), secid))
        self._rows = rows
        self.store = FakeStore(rows)
        self.market_data = object()
        self._verified = verified

    def is_range_verified(self, symbol: str, *, timeframe: str, from_date: str, till_date: str):
        return self._verified and timeframe == "D1"

    def session_dates(self, symbol: str, *, from_date: str, till_date: str):
        return tuple(
            date.fromisoformat(day)
            for day, _ in self._rows
            if from_date[:10] <= day <= till_date[:10]
        )


def test_planner_finds_shortest_equal_contiguous_windows() -> None:
    plan = plan_contiguous_equal_windows(
        FakeHistory(),
        "BR",
        available_from="2019-01-01",
        holdout_end="2024-12-31",
        minimum_observations=3,
        minimum_window_years=2,
        maximum_window_years=3,
        step_sessions=5,
        max_points=100,
    )
    assert plan.status == "FEASIBLE"
    assert plan.window_years == 3
    assert plan.evaluated_window_years == (2, 3)
    assert plan.development is not None
    assert plan.holdout is not None
    assert plan.development.from_date == "2019-01-01"
    assert plan.development.till_date == "2021-12-31"
    assert plan.holdout.from_date == "2022-01-01"
    assert plan.holdout.till_date == "2024-12-31"
    assert plan.development.passes is True
    assert plan.holdout.passes is True
    payload = plan.to_dict()
    assert payload["model_status"] == "NOT_EVALUATED"
    assert payload["data_source"] == "VERIFIED_STORED_SESSION_CONTRACT_MAP_ONLY"


def test_planner_reports_insufficient_capacity_without_moving_threshold() -> None:
    plan = plan_contiguous_equal_windows(
        FakeHistory(),
        "BR",
        available_from="2019-01-01",
        holdout_end="2024-12-31",
        minimum_observations=4,
        minimum_window_years=2,
        maximum_window_years=3,
        step_sessions=5,
        max_points=100,
    )
    assert plan.status == "INSUFFICIENT_STORED_RANGE_OR_CAPACITY"
    assert plan.window_years is None
    assert plan.minimum_observations == 4
    assert plan.evaluated_window_years == (2, 3)


def test_planner_fails_closed_when_required_d1_range_is_not_verified() -> None:
    with pytest.raises(RuntimeError, match="already-verified D1 range"):
        plan_contiguous_equal_windows(
            FakeHistory(verified=False),
            "BR",
            available_from="2019-01-01",
            holdout_end="2024-12-31",
            minimum_observations=3,
            minimum_window_years=2,
            maximum_window_years=3,
        )


def test_planner_requires_year_end_holdout() -> None:
    with pytest.raises(ValueError, match="calendar year end"):
        plan_contiguous_equal_windows(
            FakeHistory(),
            "BR",
            available_from="2019-01-01",
            holdout_end="2024-06-30",
        )
