import pytest

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


@pytest.mark.parametrize("timeframes,from_date,till_date", [
    (["D1", "H1"], "2026-11-01", "2027-01-15"),
    (["M15", "D1"], "2024-12-20", "2025-02-20"),
    (["D1", "h1", "m15"], "2021-01-01", "2022-12-31"),
])
def test_bulk_backfill_checks_full_range_before_any_partial_month(
    timeframes, from_date, till_date,
):
    history = FakeHistory()
    with pytest.raises(ValueError, match="2025-01-01..2026-12-31"):
        HistoricalBackfillService(history).run(  # type: ignore[arg-type]
            ["SBER", "Si"], timeframes,
            from_date=from_date, till_date=till_date, max_windows=1,
        )
    assert history.calls == []


def test_bulk_d1_only_2021_2022_still_allowed():
    history = FakeHistory()
    result = HistoricalBackfillService(history).run(  # type: ignore[arg-type]
        ["SBER"], ["D1"], from_date="2021-01-01",
        till_date="2022-12-31", max_windows=1,
    )
    assert result.status == "PASS"
    assert history.calls == [(("SBER",), ("D1",), "2021-01-01", "2021-01-31")]


@pytest.mark.parametrize("timeframes", ["D1,H1", "M15,D1"])
def test_legacy_ydb_cli_rejects_old_intraday_before_credentials(
    monkeypatch, capsys, timeframes,
):
    import sys
    import scripts.run_authorized_ydb_backfill as cli

    def forbidden(*args, **kwargs):
        pytest.fail("out-of-scope CLI reached YDB/token or storage")
    monkeypatch.setattr(cli, "_token", forbidden)
    monkeypatch.setattr(cli.ydb, "Driver", forbidden)
    monkeypatch.setattr(sys, "argv", [
        "run_authorized_ydb_backfill.py",
        "--connection-string", "unused",
        "--from-date", "2021-01-01",
        "--till-date", "2022-12-31",
        "--timeframes", timeframes,
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "2025-01-01..2026-12-31" in capsys.readouterr().err
