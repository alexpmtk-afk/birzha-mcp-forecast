"""SBER research batch invariants. No external network and no production DB."""
from datetime import date
from dataclasses import replace
import json

import duckdb
import pytest

import scripts.g2_sber_history_batch as batch
from test_g2_intraday_research_cli import FakeSource
from test_research_intraday_archive import hourly
from birzha.application.research_intraday_archive import ResearchIntradayArchive


def directory(tmp_path):
    root = tmp_path / "research" / "SBER"
    root.mkdir(parents=True)
    return root / batch.ARCHIVE_NAME


def args(path, *, mode="ingest", from_date="2025-06-09", through="2025-06-09"):
    base = ["--mode", mode, "--from-date", from_date, "--through-date", through]
    if mode in ("ingest", "report"):
        base += ["--archive", str(path)]
    if mode == "ingest":
        base += ["--ack-research-write", "--ack-source-verified", "--pause-seconds", "0"]
    return base


def test_plan_does_not_create_db_or_call_market(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(batch.MarketDataService, "default",
                        lambda: pytest.fail("offline plan opened provider"))
    monkeypatch.setattr(batch, "ResearchIntradayArchive",
                        lambda *_: pytest.fail("offline plan opened DB"))
    assert batch.run(["--from-date", "2025-01-01", "--through-date", "2026-12-31"]) == 0
    text = capsys.readouterr().out
    assert "PLAN_ONLY_NO_NETWORK_NO_DB" in text
    assert '"calendar_days": 730' in text
    assert not list(tmp_path.rglob("*.duckdb"))


@pytest.mark.parametrize("start,end", [
    ("2024-12-31", "2025-06-09"),
    ("2025-06-09", "2027-01-01"),
    ("2026-03-01", "2026-02-28"),
])
def test_scope_rejected_before_provider(monkeypatch, start, end):
    monkeypatch.setattr(batch.MarketDataService, "default",
                        lambda: pytest.fail("invalid scope called MOEX"))
    with pytest.raises(SystemExit) as exc:
        batch.run(["--from-date", start, "--through-date", end])
    assert exc.value.code == 2


def test_future_source_date_and_unguarded_bulk_fail_closed(monkeypatch):
    monkeypatch.setattr(batch.MarketDataService, "default",
                        lambda: pytest.fail("future/unguarded reached MOEX"))
    with pytest.raises(SystemExit):
        batch.run(["--mode", "probe", "--from-date", "2026-12-30",
                   "--through-date", "2026-12-31"])
    with pytest.raises(SystemExit):
        batch.run(["--mode", "probe", "--from-date", "2025-06-09",
                   "--through-date", "2025-06-09", "--max-days", "6"])


def test_wrong_paths_no_network_and_no_file_created(monkeypatch, tmp_path):
    monkeypatch.setattr(batch.MarketDataService, "default",
                        lambda: pytest.fail("bad archive path reached MOEX"))
    candidate = tmp_path / batch.ARCHIVE_NAME
    with pytest.raises(SystemExit):
        batch.run(args(candidate))
    assert not candidate.exists()


def test_sber_batch_ingest_skips_same_day_without_repeating_candles(monkeypatch, tmp_path, capsys):
    path = directory(tmp_path)
    fake = FakeSource()
    monkeypatch.setattr(batch.MarketDataService, "default", lambda: fake)
    assert batch.run(args(path)) == 0
    assert [row[0] for row in fake.calls] == ["H1", "M1"]
    assert batch.run(args(path)) == 0
    assert [row[0] for row in fake.calls] == ["H1", "M1"]
    assert batch.run(args(path, mode="report")) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["native_h1_stored"] == 1
    assert report["strict_m15_stored"] == 1
    assert report["duplicates"] == 0
    assert report["counts_by_status"] == {"CAPTURED_SOURCE_RESPONSE": 1}
    assert report["session_trade_date_mapping"] == "UNVERIFIED"
    assert report["unresolved_dates"] == []


def test_missing_input_journal_is_durable_and_retry_is_explicit(monkeypatch, tmp_path, capsys):
    path = directory(tmp_path)
    fake = FakeSource(gap=True)
    monkeypatch.setattr(batch.MarketDataService, "default", lambda: fake)
    assert batch.run(args(path)) == 1
    assert [x[0] for x in fake.calls] == ["H1", "M1"]
    assert batch.run(args(path)) == 0
    assert len(fake.calls) == 2
    assert batch.run(args(path, mode="report")) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["counts_by_status"] == {"MISSING_INPUTS": 1}
    assert report["unresolved_dates"][0]["date"] == "2025-06-09"
    assert report["unresolved_dates"][0]["incomplete_m15"] == 1
    assert report["unresolved_dates"][0]["attempts"] == 1
    gaps = json.loads(report["unresolved_dates"][0]["detail"])["m15_gap_events"]
    assert len(gaps) == 1 and gaps[0]["kind"] == "INCOMPLETE_M15"
    assert len(gaps[0]["missing_m1"]) == 1
    fake.gap = False
    assert batch.run(args(path) + ["--retry-missing"]) == 0
    assert len(fake.calls) == 4
    assert batch.run(args(path, mode="report")) == 0
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["strict_m15_stored"] == 1
    assert report["counts_by_status"] == {"CAPTURED_SOURCE_RESPONSE": 1}
    assert report["unresolved_dates"] == []


def test_calendar_weekend_not_filtered_out(monkeypatch, capsys):
    fake = FakeSource()
    monkeypatch.setattr(batch.MarketDataService, "default", lambda: fake)
    code = batch.run(["--mode", "probe", "--from-date", "2025-06-07",
                      "--through-date", "2025-06-08", "--max-days", "2",
                      "--pause-seconds", "0"])
    # The fake source's fixtures all happen to have 2025-06-09 timestamps.
    # Both weekend CIVIL days must still be requested; never assume CLOSED.
    assert code == 1
    assert len(fake.calls) == 4
    assert "EMPTY_SOURCE_CALENDAR_UNVERIFIED" in capsys.readouterr().out


def test_existing_non_research_db_is_untouched(monkeypatch, tmp_path):
    path = directory(tmp_path)
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE working_prices (x INTEGER)")
    conn.execute("INSERT INTO working_prices VALUES (42)")
    conn.close()
    monkeypatch.setattr(batch.MarketDataService, "default",
                        lambda: pytest.fail("unmarked archive reached MOEX"))
    with pytest.raises(ValueError, match="without research archive marker"):
        batch.run(args(path))
    conn = duckdb.connect(str(path), read_only=True)
    try:
        assert conn.execute("SELECT * FROM working_prices").fetchall() == [(42,)]
    finally:
        conn.close()


def test_report_is_read_only_and_does_not_create_missing_archive(tmp_path):
    path = directory(tmp_path)
    with pytest.raises(ValueError, match="no existing SBER archive"):
        batch.run(args(path, mode="report"))
    assert not path.exists()


def test_reject_marked_but_mixed_market_archive_before_any_write(monkeypatch, tmp_path):
    path = directory(tmp_path)
    store = ResearchIntradayArchive(str(path))
    try:
        sample = hourly()
        other = replace(sample, instrument=replace(
            sample.instrument, symbol="IMOEX", secid="IMOEX",
            engine="stock", market="index", board="SNDX",
        ))
        assert store.ingest(other)["inserted"] == 1
    finally:
        store.close()
    monkeypatch.setattr(batch.MarketDataService, "default",
                        lambda: pytest.fail("mixed archive reached provider"))
    with pytest.raises(ValueError, match="other-market"):
        batch.run(args(path))
    with pytest.raises(ValueError, match="other-market"):
        batch.run(args(path, mode="report"))
    reopened = duckdb.connect(str(path), read_only=True)
    try:
        assert reopened.execute(
            "SELECT symbol,secid FROM research_intraday_candles"
        ).fetchall() == [("IMOEX", "IMOEX")]
        assert reopened.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name='research_sber_batch_dates'"
        ).fetchone()[0] == 0
    finally:
        reopened.close()
