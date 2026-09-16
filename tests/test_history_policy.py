from __future__ import annotations

from datetime import UTC, datetime

import pytest

from birzha.application.history_policy import (
    D1_ARCHIVE_START,
    D1_SAFE_PUBLICATION_TIME,
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


def test_archive_never_certifies_current_moscow_day_at_session_close() -> None:
    # 2026-09-15 20:56 UTC == 23:56 MSK.  A completed trading session is not
    # yet treated as safely published historical ISS data on that same day.
    assert latest_safe_d1_calendar_date(
        datetime(2026, 9, 15, 20, 56, tzinfo=UTC)
    ) == "2026-09-14"


def test_archive_waits_for_next_morning_publication_buffer() -> None:
    assert D1_SAFE_PUBLICATION_TIME.hour == 10

    # 2026-09-16 06:59 UTC == 09:59 MSK: yesterday is still inside the
    # publication buffer, so we retain the older bound.
    assert latest_safe_d1_calendar_date(
        datetime(2026, 9, 16, 6, 59, tzinfo=UTC)
    ) == "2026-09-14"

    # At 10:00 MSK the previous calendar day becomes eligible.  The MOEX
    # session calendar still decides whether that upper-bound date traded.
    assert latest_safe_d1_calendar_date(
        datetime(2026, 9, 16, 7, 0, tzinfo=UTC)
    ) == "2026-09-15"
