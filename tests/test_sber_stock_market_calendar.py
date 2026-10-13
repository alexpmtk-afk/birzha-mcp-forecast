"""Official 2025/26 MOEX stock-market notice rules; no live network."""
from datetime import date

import pytest

from birzha.application.sber_stock_market_calendar import (
    published_stock_civil_day, MARKET_CLOSED_HOLIDAYS, WEEKEND_NO_SESSION,
    SOURCES,
)
import scripts.g2_sber_history_batch as batch
from birzha.application.research_intraday_archive import ResearchIntradayArchive


@pytest.mark.parametrize("civil_date,status,is_open,is_weekend", [
    ("2025-01-01", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
    ("2025-01-03", "MARKET_OPEN_HOLIDAY_NOTICE", True, False),
    ("2025-02-22", "MARKET_CLOSED_BEFORE_WEEKEND_START", False, False),
    ("2025-03-01", "MARKET_OPEN_ADDITIONAL_WEEKEND", True, True),
    ("2025-03-08", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
    ("2025-03-22", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2025-06-07", "MARKET_OPEN_ADDITIONAL_WEEKEND", True, True),
    ("2025-11-01", "MARKET_OPEN_REGULAR_SATURDAY", True, False),
    ("2025-11-02", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2025-11-03", "MARKET_OPEN_HOLIDAY_NOTICE", True, False),
    ("2025-12-31", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
    ("2026-01-03", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
    ("2026-01-05", "MARKET_OPEN_HOLIDAY_NOTICE", True, False),
    ("2026-03-08", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
    ("2026-06-15", "MARKET_OPEN_REGULAR_WEEKDAY", True, False),
    ("2026-06-20", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2026-07-18", "MARKET_OPEN_ADDITIONAL_WEEKEND", True, True),
    ("2026-10-11", "MARKET_OPEN_ADDITIONAL_WEEKEND", True, True),
    ("2026-10-24", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2026-12-31", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
])
def test_published_civil_market_calendar(civil_date,status,is_open,is_weekend):
    day=published_stock_civil_day(date.fromisoformat(civil_date))
    assert (day.market_schedule,day.is_market_scheduled,day.is_additional_weekend) == (
        status,is_open,is_weekend
    )
    assert day.source in SOURCES[day.civil_date[:4] and int(day.civil_date[:4])].values()
    assert day.session_trade_date=="UNVERIFIED"
    assert day.instrument_session_eligibility=="NOT_PIT_VERIFIED"


@pytest.mark.parametrize("invalid", ["2024-12-31", "2027-01-01"])
def test_out_of_contract_date_is_rejected(invalid):
    with pytest.raises(ValueError,match="2025-2026"):
        published_stock_civil_day(date.fromisoformat(invalid))


def test_official_weekend_exceptions_are_weekends_and_nonoverlap():
    for year, dates in WEEKEND_NO_SESSION.items():
        assert all(d.year==year and d.weekday()>=5 for d in dates)
        assert not dates & MARKET_CLOSED_HOLIDAYS[year] - dates  # no mutation


def test_probe_skips_published_closed_days_without_contacting_provider(monkeypatch,capsys):
    monkeypatch.setattr(batch.MarketDataService,"default",
                        lambda: pytest.fail("MOEX called on explicitly closed day"))
    assert batch.run(["--mode","probe","--from-date","2025-06-12",
                      "--through-date","2025-06-12"]) == 0
    assert "SKIPPED_PUBLISHED_MARKET_CLOSED" in capsys.readouterr().out


def test_report_distinguishes_published_closed_and_open_without_network(tmp_path):
    path=tmp_path/"research"/"SBER"/batch.ARCHIVE_NAME
    path.parent.mkdir(parents=True)
    archive=ResearchIntradayArchive(str(path))
    archive.close()
    report=batch.report(path,date(2025,6,11),date(2025,6,13))
    assert report["civil_days"]==3
    assert report["published_market_closed_days"]==1
    assert report["scheduled_market_civil_days"]==2
    assert report["counts_by_status"]=={
        "UNVISITED":2,
        "MARKET_CLOSED_PUBLISHED_NO_FETCH":1
    }
    assert [row["date"] for row in report["unresolved_dates"]]==[
        "2025-06-11","2025-06-13"
    ]
    assert report["unexpected_closed_captures"]==[]
    assert report["session_trade_date_mapping"]=="UNVERIFIED"
