"""Conservative civil-date stock MARKET schedule from dated official MOEX notices.

The published exchange-wide calendar is NOT a point-in-time confirmation that
SBER traded in every allowed session, or that all expected H1/M15 exist.
Weekend sessions are a part of the *following exchange trading day*; this
module does not guess their formal trading-date identifier or stitch candles.
Updates/cancellations to published schedules require explicit review.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

SOURCES = {
    2025: {
        "holiday": "https://www.moex.com/n73701",
        "weekend": "https://www.moex.com/n77967",
    },
    2026: {
        "holiday": "https://www.moex.com/n94172",
        "weekend": "https://www.moex.com/n95564",
    },
}

def _dates(year: int, mmdd: str) -> frozenset[date]:
    return frozenset(date.fromisoformat(f"{year}-{item}") for item in mmdd.split())

# Official stock market holiday notices. This is not the Russian work calendar:
# trading occurs on some designated civil holidays.
MARKET_CLOSED_HOLIDAYS = {
    2025: _dates(2025, "01-01 01-02 01-07 02-23 03-08 05-01 05-09 06-12 11-04 12-31"),
    2026: _dates(2026, "01-01 01-02 01-03 01-04 01-07 02-23 03-08 05-01 05-09 06-12 11-04 12-31"),
}
HOLIDAY_TRADING_DAYS = {
    2025: _dates(2025, "01-03 01-06 01-08 05-02 05-08 06-13 11-03"),
    2026: _dates(2026, "01-05 01-06 01-08 01-09 03-09 05-11"),
}
# 2025 weekend sessions from 2025-03-01, except these dates.
WEEKEND_NO_SESSION = {
    2025: _dates(
        2025,
        "03-08 03-09 03-22 03-23 04-19 04-20 05-24 05-25 "
        "06-21 06-22 08-02 08-03 09-20 09-21 10-25 10-26 "
        "11-02 12-06 12-07"
    ),
    2026: _dates(
        2026,
        "01-03 01-04 01-10 01-11 02-14 02-15 03-07 03-08 "
        "03-21 03-22 05-09 05-10 06-20 06-21 08-01 08-02 "
        "08-15 08-16 09-12 09-13 10-24 10-25 12-05 12-06"
    ),
}
REGULAR_WORKING_SATURDAY = frozenset({date(2025, 11, 1)})


@dataclass(frozen=True)
class PublishedStockCivilDay:
    civil_date: str
    market_schedule: str
    source: str
    is_market_scheduled: bool
    is_additional_weekend: bool
    session_trade_date: str = "UNVERIFIED"
    instrument_session_eligibility: str = "NOT_PIT_VERIFIED"


def published_stock_civil_day(day: date) -> PublishedStockCivilDay:
    if not date(2025, 1, 1) <= day <= date(2026, 12, 31):
        raise ValueError("official research market schedule limited to 2025-2026")
    year = day.year
    holiday_source = SOURCES[year]["holiday"]
    weekend_source = SOURCES[year]["weekend"]
    if day in MARKET_CLOSED_HOLIDAYS[year]:
        return PublishedStockCivilDay(day.isoformat(), "MARKET_CLOSED_HOLIDAY_NOTICE",
                                      holiday_source, False, False)
    if day in REGULAR_WORKING_SATURDAY:
        return PublishedStockCivilDay(day.isoformat(), "MARKET_OPEN_REGULAR_SATURDAY",
                                      holiday_source, True, False)
    if day in HOLIDAY_TRADING_DAYS[year]:
        return PublishedStockCivilDay(day.isoformat(), "MARKET_OPEN_HOLIDAY_NOTICE",
                                      holiday_source, True, False)
    if day.weekday() >= 5:
        if year == 2025 and day < date(2025, 3, 1):
            return PublishedStockCivilDay(day.isoformat(), "MARKET_CLOSED_BEFORE_WEEKEND_START",
                                          weekend_source, False, False)
        if day in WEEKEND_NO_SESSION[year]:
            return PublishedStockCivilDay(day.isoformat(), "MARKET_CLOSED_WEEKEND_NOTICE",
                                          weekend_source, False, False)
        return PublishedStockCivilDay(day.isoformat(), "MARKET_OPEN_ADDITIONAL_WEEKEND",
                                      weekend_source, True, True)
    return PublishedStockCivilDay(day.isoformat(), "MARKET_OPEN_REGULAR_WEEKDAY",
                                  holiday_source, True, False)
