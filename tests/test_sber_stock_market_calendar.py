"""Official 2025/26 MOEX stock-market notice rules; no live network."""
from datetime import date, timedelta
from dataclasses import replace

import pytest

from birzha.application.sber_stock_market_calendar import (
    published_stock_civil_day, MARKET_CLOSED_HOLIDAYS, WEEKEND_NO_SESSION,
    SOURCES,
)
import scripts.g2_sber_history_batch as batch
from birzha.application.research_intraday_archive import ResearchIntradayArchive
from test_research_intraday_archive import hourly


@pytest.mark.parametrize("civil_date,status,is_open,is_weekend", [
    ("2025-01-01", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
    ("2025-01-03", "MARKET_OPEN_HOLIDAY_NOTICE", True, False),
    ("2025-02-22", "MARKET_CLOSED_BEFORE_WEEKEND_START", False, False),
    ("2025-03-01", "MARKET_OPEN_ADDITIONAL_WEEKEND", True, True),
    ("2025-03-08", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
    ("2025-03-22", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2025-06-07", "MARKET_OPEN_ADDITIONAL_WEEKEND", True, True),
    ("2025-08-09", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2025-08-10", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2025-11-22", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2025-11-23", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
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
    ("2026-11-04", "MARKET_OPEN_HOLIDAY_NOTICE", True, False),
    ("2026-11-28", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2026-11-29", "MARKET_CLOSED_WEEKEND_NOTICE", False, False),
    ("2026-12-31", "MARKET_CLOSED_HOLIDAY_NOTICE", False, False),
])
def test_published_civil_market_calendar(civil_date,status,is_open,is_weekend):
    day=published_stock_civil_day(date.fromisoformat(civil_date))
    assert (day.market_schedule,day.is_market_scheduled,day.is_additional_weekend) == (
        status,is_open,is_weekend
    )
    assert day.source in SOURCES[int(day.civil_date[:4])].values()
    assert day.session_trade_date=="UNVERIFIED"
    assert day.instrument_session_eligibility=="NOT_PIT_VERIFIED"


@pytest.mark.parametrize("invalid", ["2024-12-31", "2027-01-01"])
def test_out_of_contract_date_is_rejected(invalid):
    with pytest.raises(ValueError,match="2025-2026"):
        published_stock_civil_day(date.fromisoformat(invalid))


def test_official_weekend_exceptions_are_weekends_and_nonoverlap():
    for year, dates in WEEKEND_NO_SESSION.items():
        assert all(d.year==year and d.weekday()>=5 for d in dates)
        assert len(dates) >= 16


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


def test_published_stock_market_schedule_partition_through_2026_oct12():
    # A calendar CIVIL day is not a unique MOEX exchange trade-date.
    current, last = date(2025, 1, 1), date(2026, 10, 12)
    scheduled = closed = weekend_scheduled = 0
    while current <= last:
        item = published_stock_civil_day(current)
        scheduled += int(item.is_market_scheduled)
        closed += int(not item.is_market_scheduled)
        weekend_scheduled += int(item.is_additional_weekend)
        current += timedelta(days=1)
    assert (scheduled, closed, weekend_scheduled) == (577, 73, 126)
    assert scheduled + closed == 650


def test_report_flags_closed_day_stored_candles_even_without_checkpoint(tmp_path):
    path = tmp_path / "research" / "SBER" / batch.ARCHIVE_NAME
    path.parent.mkdir(parents=True)
    store = ResearchIntradayArchive(str(path))
    try:
        original = hourly()
        candle = replace(original.candles[0],
                         begin="2025-06-12T10:00:00+03:00",
                         end="2025-06-12T10:59:59+03:00")
        store.ingest(replace(original, candles=(candle,)))
        # Deliberately NO capture checkpoint, to expose orphan rows.
    finally:
        store.close()
    outcome = batch.report(path, date(2025, 6, 12), date(2025, 6, 12))
    assert outcome["counts_by_status"] == {"CALENDAR_SOURCE_CONFLICT": 1}
    assert outcome["published_market_closed_days"] == 1
    assert outcome["native_h1_stored"] == 1
    assert outcome["unresolved_dates"] == []
    assert len(outcome["unexpected_closed_captures"]) == 1
    assert outcome["unexpected_closed_captures"][0]["stored_candles"] == 1
    assert outcome["unexpected_closed_captures"][0]["date"] == "2025-06-12"


@pytest.mark.parametrize("date_string,bulletin", [
    ("2025-08-09", "https://www.moex.com/n92346"),
    ("2025-08-10", "https://www.moex.com/n92346"),
    ("2025-11-22", "https://www.moex.com/n95363"),
    ("2025-11-23", "https://www.moex.com/n95363"),
    ("2026-11-04", "https://www.moex.com/n104837"),
    ("2026-11-28", "https://www.moex.com/n104837"),
    ("2026-11-29", "https://www.moex.com/n104837"),
])
def test_updated_publication_precedence(date_string,bulletin):
    entry=published_stock_civil_day(date.fromisoformat(date_string))
    assert entry.source == bulletin


@pytest.mark.parametrize("day", ["2025-08-09","2025-08-10","2025-11-22","2025-11-23"])
def test_reconcile_empty_journal_to_published_exchange_closure(day,tmp_path):
    path=tmp_path/"research"/"SBER"/batch.ARCHIVE_NAME
    path.parent.mkdir(parents=True)
    store=ResearchIntradayArchive(str(path))
    try:
        store.conn.execute(batch.SCHEMA)
        batch.record(store.conn,day,"EMPTY_SOURCE_CALENDAR_UNVERIFIED",{},
                     '{"note":"historical source returned empty"}')
    finally:
        store.close()
    result=batch.report(path,date.fromisoformat(day),date.fromisoformat(day))
    assert result["published_market_closed_days"]==1
    assert result["scheduled_market_civil_days"]==0
    assert result["counts_by_status"]=={"MARKET_CLOSED_RECONCILED_EMPTY_SOURCE":1}
    assert result["unresolved_dates"]==[]
    assert result["unexpected_closed_captures"]==[]
    assert result["reconciled_closed_source_empty"]==[{
        "date":day,
        "previous_journal_status":"EMPTY_SOURCE_CALENDAR_UNVERIFIED",
        "source":published_stock_civil_day(date.fromisoformat(day)).source,
        "attempts":1,
    }]


@pytest.mark.parametrize("status", ["MISSING_INPUTS","ERROR"])
def test_closed_day_nonempty_error_journal_not_hidden(status,tmp_path):
    path=tmp_path/"research"/"SBER"/batch.ARCHIVE_NAME
    path.parent.mkdir(parents=True)
    store=ResearchIntradayArchive(str(path))
    try:
        store.conn.execute(batch.SCHEMA)
        batch.record(store.conn,"2025-08-09",status,{},'original audit evidence')
    finally:
        store.close()
    result=batch.report(path,date(2025,8,9),date(2025,8,9))
    assert result["counts_by_status"]=={"CALENDAR_SOURCE_CONFLICT":1}
    assert result["reconciled_closed_source_empty"]==[]
    assert result["unexpected_closed_captures"][0]["evidence_status"]==status


@pytest.mark.parametrize("day", ["2025-08-09","2025-11-22"])
def test_revised_cancelled_session_never_calls_source(monkeypatch,day,capsys):
    monkeypatch.setattr(batch.MarketDataService,"default",
                        lambda: pytest.fail("source called for revised exchange closure"))
    assert batch.run(["--mode","probe","--from-date",day,
                      "--through-date",day])==0
    assert "SKIPPED_PUBLISHED_MARKET_CLOSED" in capsys.readouterr().out
