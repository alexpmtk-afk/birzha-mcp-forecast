from birzha.application.backfill import HistoricalBackfillService, monthly_windows


class FakeHistory:
    def __init__(self, *, partial_month: str | None = None):
        self.calls = []
        self.partial_month = partial_month

    def sync_many(self, symbols, timeframes, *, from_date, till_date):
        self.calls.append((tuple(symbols), tuple(timeframes), from_date, till_date))
        status = "PARTIAL" if from_date == self.partial_month else "PASS"
        return {"status": status, "requested": len(symbols) * len(timeframes)}


def test_monthly_windows_are_calendar_bounded():
    windows = monthly_windows("2026-01-17", "2026-03-04")
    assert [(item.from_date, item.till_date) for item in windows] == [
        ("2026-01-17", "2026-01-31"),
        ("2026-02-01", "2026-02-28"),
        ("2026-03-01", "2026-03-04"),
    ]


def test_backfill_stops_after_requested_number_of_windows():
    history = FakeHistory()
    report = HistoricalBackfillService(history).run(  # type: ignore[arg-type]
        ["Si", "BR"], ["D1", "H1"],
        from_date="2026-01-01", till_date="2026-03-31", max_windows=2,
    )
    assert report.status == "PASS"
    assert report.complete is False
    assert report.processed_windows == 2
    assert report.next_from_date == "2026-03-01"
    assert len(history.calls) == 2


def test_backfill_stops_on_partial_and_retries_same_month():
    history = FakeHistory(partial_month="2026-02-01")
    report = HistoricalBackfillService(history).run(  # type: ignore[arg-type]
        ["BR"], ["D1"],
        from_date="2026-01-01", till_date="2026-03-31", max_windows=3,
    )
    assert report.status == "PARTIAL"
    assert report.complete is False
    assert report.processed_windows == 2
    assert report.next_from_date == "2026-02-01"
    assert [call[2] for call in history.calls] == ["2026-01-01", "2026-02-01"]
