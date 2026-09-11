from __future__ import annotations

from datetime import date, timedelta

import scripts.run_authorized_ydb_prepare_validation as prepare


class FakeHistory:
    def __init__(self, counts: dict[tuple[str, str], int]) -> None:
        self.counts = counts

    def session_dates(self, symbol: str, *, from_date: str, till_date: str):
        period = "development" if till_date == "2024-06-30" else "holdout"
        count = self.counts.get((period, symbol), 500)
        start = date.fromisoformat(from_date[:10])
        return tuple(start + timedelta(days=index) for index in range(count))


def test_session_capacity_checks_development_and_holdout_for_all_markets() -> None:
    counts = {
        ("development", "GOLD"): 390,
        ("holdout", "RTSI"): 395,
    }
    capacity, shortfall = prepare._session_capacity(
        FakeHistory(counts),  # type: ignore[arg-type]
        validation_start="2022-06-01",
        split_date="2024-06-30",
        validation_end="2026-05-31",
        step_sessions=5,
        max_points=80,
    )

    assert set(capacity) == {"development", "holdout"}
    assert set(capacity["development"]) == set(prepare.CORE_VALIDATION_SYMBOLS)
    assert set(capacity["holdout"]) == set(prepare.CORE_VALIDATION_SYMBOLS)
    assert shortfall["development"]["GOLD"]["20"] < 20
    assert shortfall["holdout"]["RTSI"]["20"] < 20
    assert "SBER" not in shortfall["development"]
    assert "SBER" not in shortfall["holdout"]


def test_session_capacity_accepts_periods_with_enough_sessions() -> None:
    capacity, shortfall = prepare._session_capacity(
        FakeHistory({}),  # type: ignore[arg-type]
        validation_start="2022-06-01",
        split_date="2024-06-30",
        validation_end="2026-05-31",
        step_sessions=5,
        max_points=80,
    )

    assert shortfall == {}
    for period in ("development", "holdout"):
        for symbol in prepare.CORE_VALIDATION_SYMBOLS:
            observations = capacity[period][symbol]["non_overlapping_observations"]
            assert observations["20"] == 20
