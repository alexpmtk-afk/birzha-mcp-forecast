from dataclasses import replace

import pytest

import scripts.g2_intraday_research_archive as cli
from test_research_intraday_archive import FakeMarket, instrument
from birzha.application.research_intraday_fetch import prepare_one_day


def test_plan_is_offline_and_does_not_open_storage(monkeypatch, capsys):
    def forbidden():
        pytest.fail("plan must not open MOEX")
    monkeypatch.setattr(cli.MarketDataService, "default", forbidden)
    monkeypatch.setattr(cli, "ResearchIntradayArchive", forbidden)
    assert cli.run(["--date", "2025-06-09"]) == 0
    printed = capsys.readouterr().out
    assert "PLAN_ONLY_NO_NETWORK_NO_WRITE" in printed
    assert '"raw_M1_persisted": false' in printed
    assert printed.index("SBER") < printed.index("RTSI")


@pytest.mark.parametrize("mode", ["plan", "probe", "ingest"])
def test_forbidden_historical_year_rejected_before_network_or_storage(monkeypatch, mode):
    monkeypatch.setattr(cli.MarketDataService, "default",
                        lambda: pytest.fail("forbidden date reached network"))
    monkeypatch.setattr(cli, "ResearchIntradayArchive",
                        lambda *_: pytest.fail("forbidden date reached DB"))
    for date in ("2024-12-31", "2027-01-01"):
        args = ["--date", date, "--mode", mode]
        if mode == "ingest":
            args += ["--archive", "birzha_intraday_research_2025_2026.duckdb",
                     "--ack-research-write"]
        with pytest.raises(SystemExit) as exc:
            cli.run(args)
        assert exc.value.code == 2


def test_ingest_needs_explicit_ack_and_dedicated_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.MarketDataService, "default",
                        lambda: pytest.fail("unsafe ingest reached provider"))
    for options in (
        ["--archive", str(tmp_path/cli.ARCHIVE_NAME)],
        ["--archive", str(tmp_path/"birzha_history.duckdb"), "--ack-research-write"],
    ):
        with pytest.raises(SystemExit) as exc:
            cli.run(["--mode", "ingest", "--date", "2025-06-09"] + options)
        assert exc.value.code == 2


class FakeSource(FakeMarket):
    def resolve(self, symbol, *, as_of):
        return instrument()


def test_probe_does_not_write_and_reads_only_day(monkeypatch, capsys):
    fake = FakeSource()
    monkeypatch.setattr(cli.MarketDataService, "default", lambda: fake)
    monkeypatch.setattr(cli, "ResearchIntradayArchive",
                        lambda *_: pytest.fail("probe must not open DB"))
    assert cli.run(["--date", "2025-06-09", "--mode", "probe",
                    "--markets", "SBER"]) == 0
    printed = capsys.readouterr().out
    assert '"status": "SOURCE_SAMPLE_PASS"' in printed
    assert '"m15_rows": 1' in printed
    assert [x[0] for x in fake.calls] == ["H1", "M1"]


def test_ingest_creates_only_the_named_research_archive(monkeypatch, tmp_path):
    fake = FakeSource()
    monkeypatch.setattr(cli.MarketDataService, "default", lambda: fake)
    archive = tmp_path/cli.ARCHIVE_NAME
    code = cli.run([
        "--mode", "ingest", "--date", "2025-06-09",
        "--markets", "SBER", "--archive", str(archive),
        "--ack-research-write",
    ])
    assert code == 0 and archive.exists()
    first_fetches = list(fake.calls)
    assert len(first_fetches) == 2
    assert list(tmp_path.glob("*.duckdb")) == [archive]
    from birzha.application.research_intraday_archive import ResearchIntradayArchive
    stored = ResearchIntradayArchive(str(archive))
    try:
        assert len(stored.inventory()) == 2
        assert all(item["timeframe"] in ("H1", "M15") for item in stored.inventory())
    finally:
        stored.close()
    # Same prices are idempotent and must not double the archive row count.
    assert cli.run([
        "--mode", "ingest", "--date", "2025-06-09",
        "--markets", "SBER", "--archive", str(archive),
        "--ack-research-write",
    ]) == 0
    assert fake.calls == first_fetches, "checkpoint must prevent second M1/H1 download"
    stored = ResearchIntradayArchive(str(archive))
    try:
        assert sum(x["rows"] for x in stored.inventory()) == 2
    finally:
        stored.close()


def test_partial_source_is_not_checkpointed_and_can_be_retried(monkeypatch, tmp_path):
    fake = FakeSource(gap=True)
    monkeypatch.setattr(cli.MarketDataService, "default", lambda: fake)
    archive = tmp_path/cli.ARCHIVE_NAME
    args = [
        "--mode", "ingest", "--date", "2025-06-09",
        "--markets", "SBER", "--archive", str(archive),
        "--ack-research-write",
    ]
    assert cli.run(args) == 1
    assert [x[0] for x in fake.calls] == ["H1", "M1"]
    # Persist the source quality even if missing minutes may be legitimate.
    assert cli.run(args) == 0
    assert len(fake.calls) == 2, "ordinary retry must not re-download known partial source"
    # Only explicit repair is allowed to re-fetch and upgrade the checkpoint.
    fake.gap = False
    assert cli.run(args + ["--retry-partial"]) == 0
    assert len(fake.calls) == 4
    assert cli.run(args) == 0
    assert len(fake.calls) == 4
