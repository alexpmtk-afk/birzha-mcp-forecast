from __future__ import annotations

from datetime import UTC, datetime

import pytest

from birzha.application.history_policy import (
    D1_ARCHIVE_START,
    IntradayPersistenceForbiddenError,
    latest_safe_d1_calendar_date,
    require_persistent_price_timeframe,
)


def test_persistent_history_is_d1_only_from_fixed_2021_start() -> None:
    assert D1_ARCHIVE_START == "2021-01-01"
    assert require_persistent_price_timeframe("d1") == "D1"


@pytest.mark.parametrize("timeframe", ["H1", "M15"])
def test_intraday_persistence_is_forbidden(timeframe: str) -> None:
    with pytest.raises(IntradayPersistenceForbiddenError, match="D1-only"):
        require_persistent_price_timeframe(timeframe)


def test_archive_upper_bound_moves_with_current_time_not_end_of_2025() -> None:
    # 2026-09-15 18:00 UTC == 21:00 MSK, so the current trading day is
    # conservatively still forming and 2026-09-14 is the safe upper bound.
    assert latest_safe_d1_calendar_date(
        datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    ) == "2026-09-14"

    # 20:56 UTC == 23:56 MSK, after the conservative close boundary.
    # The MOEX calendar still decides whether the date is an actual session.
    assert latest_safe_d1_calendar_date(
        datetime(2026, 9, 15, 20, 56, tzinfo=UTC)
    ) == "2026-09-15"
