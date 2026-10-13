from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

import scripts.g2_si_intraday_research_batch as batch


def _d1_source(path: Path) -> Path:
    con = duckdb.connect(str(path))
    try:
        con.execute("""
            CREATE TABLE historical_candles (
                symbol VARCHAR, secid VARCHAR, engine VARCHAR, market VARCHAR,
                board VARCHAR, timeframe VARCHAR, begin VARCHAR,
                completed BOOLEAN, volume DOUBLE, value DOUBLE
            )
        """)
        con.execute("""INSERT INTO historical_candles VALUES
            ('Si','SiM5','futures','forts','RFUD','D1','2025-06-09T00:00:00+03:00',true,10,20),
            ('Si','SiU5','futures','forts','RFUD','D1','2025-06-09T00:00:00+03:00',true,11,21),
            ('Si','SiM5','futures','forts','RFUD','D1','2025-06-10T00:00:00+03:00',false,12,22),
            ('Si','SiM5','futures','forts','RFUD','M15','2025-06-09T10:00:00+03:00',true,1,2),
            ('SBER','SBER','stock','shares','TQBR','D1','2025-06-09T00:00:00+03:00',true,1,2)""")
    finally:
        con.close()
    return path


def test_read_source_is_read_only_and_keeps_exact_contract_session_keys(tmp_path):
    source = _d1_source(tmp_path / "source.duckdb")
    before = batch._sha256(source)
    contracts, d1_days = batch._read_source(source, "2025-06-01", "2025-06-30")
    assert {row["secid"] for row in contracts} == {"SiM5", "SiU5"}
    assert d1_days == {("SiM5", "2025-06-09"), ("SiU5", "2025-06-09")}
    assert batch._sha256(source) == before


def test_plan_reads_archive_but_never_creates_provider_or_research_archive(tmp_path, monkeypatch, capsys):
    source = _d1_source(tmp_path / "source.duckdb")
    before = batch._sha256(source)

    def forbidden(*args, **kwargs):
        pytest.fail("offline plan reached network or research storage")

    monkeypatch.setattr(batch.MarketDataService, "default", forbidden)
    monkeypatch.setattr(batch, "ResearchIntradayArchive", forbidden)
    result = batch.run([
        "--mode", "plan", "--from-date", "2025-06-01", "--till-date", "2025-06-30",
        "--d1-archive", str(source),
    ])
    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert report["status"] == "PLAN_ONLY_NO_NETWORK_NO_WRITE"
    assert report["d1_archive_sessions"] == 1
    assert report["calendar_checked"] is False
    assert batch._sha256(source) == before


def test_progress_cursor_is_bound_to_source_calendar_and_destination(tmp_path):
    progress = tmp_path / "progress.json"
    assert batch._load_progress(
        progress, first="2025-06-01", last="2025-06-30", d1_sha="d1",
        archive=tmp_path / batch._ARCHIVE_NAME, calendar_sha="cal",
    ) == 0
    batch._atomic_json(progress, {
        "schema": batch._PROGRESS_SCHEMA,
        "from_date": "2025-06-01", "till_date": "2025-06-30",
        "d1_archive_sha256": "d1",
        "research_archive": str((tmp_path / batch._ARCHIVE_NAME).resolve()),
        "normalized_calendar_sha256": "cal", "next_index": 7,
    })
    assert batch._load_progress(
        progress, first="2025-06-01", last="2025-06-30", d1_sha="d1",
        archive=tmp_path / batch._ARCHIVE_NAME, calendar_sha="cal",
    ) == 7
    with pytest.raises(ValueError, match="does not match"):
        batch._load_progress(
            progress, first="2025-06-01", last="2025-06-30", d1_sha="changed",
            archive=tmp_path / batch._ARCHIVE_NAME, calendar_sha="cal",
        )


def test_bounded_batch_records_partial_day_and_resumes_without_duplicate_fetch(tmp_path, monkeypatch, capsys):
    source = _d1_source(tmp_path / "source.duckdb")
    archive_path = tmp_path / batch._ARCHIVE_NAME
    date_key = "2025-06-09"
    instrument = SimpleNamespace(secid="SiM5", symbol="Si", engine="futures",
                                 market="forts", board="RFUD")
    series = SimpleNamespace(candles=(object(),), count=1)
    capture = SimpleNamespace(
        instrument=instrument, hourly=series, quarter_hour=series,
        minute_rows_seen=15, incomplete_m15_buckets=1, skipped_h1_bars=0,
    )
    calls = {"fetch": 0, "ingest": 0, "mark": 0}

    class FakeMarket:
        provider = object()

        @staticmethod
        def resolve(symbol, *, as_of):
            assert symbol == "Si" and as_of.isoformat() == date_key
            return instrument

    class FakeArchive:
        def __init__(self, path):
            assert Path(path) == archive_path
            self.captured = False

        def capture_quality(self, inst, day):
            return "CAPTURED_WITH_GAPS" if self.captured else None

        def ingest(self, received):
            calls["ingest"] += 1
            return {"inserted": 1}

        def mark_captured(self, inst, day, **kwargs):
            calls["mark"] += 1
            self.captured = True

        def close(self):
            pass

    monkeypatch.setattr(batch.MarketDataService, "default", lambda: FakeMarket())
    monkeypatch.setattr(batch, "ResearchIntradayArchive", FakeArchive)
    monkeypatch.setattr(batch, "prepare_one_day", lambda *args, **kwargs: calls.__setitem__("fetch", calls["fetch"] + 1) or capture)
    monkeypatch.setattr(batch, "_load_calendar", lambda *args, **kwargs: {
        "normalized_calendar_sha256": "calendar-test-sha",
        "sessions_by_secid": {"SiM5": [date_key]},
    })
    args = [
        "--mode", "ingest", "--from-date", date_key, "--till-date", date_key,
        "--d1-archive", str(source), "--archive", str(archive_path),
        "--max-sessions", "1", "--ack-research-write",
    ]
    assert batch.run(args) == 1
    first_output = json.loads(capsys.readouterr().out.splitlines()[0])
    assert first_output["status"] == "CAPTURED_WITH_GAPS"
    assert first_output["incomplete_m15_buckets"] == 1
    assert first_output["d1_exact_contract_present"] is True
    assert calls == {"fetch": 1, "ingest": 2, "mark": 1}
    ledger = archive_path.with_name(archive_path.stem + ".si-coverage.jsonl")
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1

    # Re-running the same bounded range resumes at the saved cursor and makes
    # no second source call or duplicate coverage receipt.
    assert batch.run(args) == 0
    capsys.readouterr()
    assert calls == {"fetch": 1, "ingest": 2, "mark": 1}
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_calendar_outage_does_not_create_archive_or_advance_progress(tmp_path, monkeypatch, capsys):
    source = _d1_source(tmp_path / "source.duckdb")
    archive_path = tmp_path / batch._ARCHIVE_NAME
    monkeypatch.setattr(batch.MarketDataService, "default", lambda: SimpleNamespace())

    def unavailable(*args, **kwargs):
        raise RuntimeError("MOEX HTTP 503")

    monkeypatch.setattr(batch, "_load_calendar", unavailable)
    result = batch.run([
        "--mode", "ingest", "--from-date", "2025-06-09", "--till-date", "2025-06-09",
        "--d1-archive", str(source), "--archive", str(archive_path),
        "--max-sessions", "1", "--ack-research-write",
    ])
    report = json.loads(capsys.readouterr().out)
    assert result == 1
    assert report["status"] == "CALENDAR_UNAVAILABLE"
    assert report["progress_advanced"] is False
    assert not archive_path.exists()
    assert not archive_path.with_name(archive_path.stem + ".si-progress.json").exists()
    assert not archive_path.with_name(archive_path.stem + ".si-coverage.jsonl").exists()
