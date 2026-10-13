from datetime import datetime, timedelta
import json
from dataclasses import replace

import pytest

import scripts.g2_imoex_month_archive as cli
from birzha.domain.market import Candle, CandleSeries, Instrument


def index():
    return Instrument(
        symbol="IMOEX", secid="IMOEX", board="SNDX",
        engine="stock", market="index", asset_class="index",
    )


def bar(start: datetime, seconds: int, close: float = 3000):
    return Candle(
        open=close, high=close+3, low=close-2, close=close+1,
        value=15000., volume=5.,
        begin=start.isoformat(), end=(start + timedelta(seconds=seconds)).isoformat(),
        completed=True,
    )


class Source:
    def __init__(self):
        self.calls = []
        self.gap = False

    def resolve(self, market, *, as_of):
        assert market == "IMOEX"
        self.calls.append("resolve")
        return index()

    def candles_for_instrument(self, instrument, *, timeframe,
                               from_date, till_date, completed_only):
        self.calls.append((timeframe, from_date, till_date))
        if timeframe == "D1":
            d = datetime.fromisoformat("2025-01-15T00:00:00+03:00")
            return CandleSeries(instrument, "D1", (bar(d, 86399),))
        if timeframe == "H1":
            d = datetime.fromisoformat("2025-01-15T10:00:00+03:00")
            return CandleSeries(instrument, "H1", (bar(d, 3599),))
        assert timeframe == "M1"
        d = datetime.fromisoformat("2025-01-15T10:00:00+03:00")
        minutes = tuple(bar(d + timedelta(minutes=i), 59, close=3000+i) for i in range(15))
        return CandleSeries(instrument, "M1", minutes[:-1] if self.gap else minutes)


def test_month_bounds_strict():
    assert cli.month_bounds(2025, 1) == ("2025-01-01", "2025-01-31")
    assert cli.month_bounds(2026, 2) == ("2026-02-01", "2026-02-28")
    for y, m in [(2024, 12), (2027, 1), (2025, 13), (2026, 0)]:
        with pytest.raises(ValueError):
            cli.month_bounds(y, m)


def test_plan_offline_no_writer(monkeypatch, capsys):
    monkeypatch.setattr(cli.MarketDataService, "default",
                        lambda: pytest.fail("plan attempted source connection"))
    monkeypatch.setattr(cli, "ResearchIntradayArchive",
                        lambda *_: pytest.fail("plan attempted file write"))
    assert cli.run(["--year", "2025", "--month", "1"]) == 0
    assert "PLAN_ONLY_NO_NETWORK_NO_WRITE" in capsys.readouterr().out


def test_write_requires_full_optin_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.MarketDataService, "default",
                        lambda: pytest.fail("missing opt-in attempted MOEX"))
    args = ["--year", "2025", "--month", "1", "--mode", "ingest",
            "--archive", str(tmp_path/cli.ARCHIVE_NAME),
            "--report", str(tmp_path/"manifest.json")]
    with pytest.raises(SystemExit) as ex:
        cli.run(args)
    assert ex.value.code == 2


def test_capture_one_d1_day_writes_valid_bars_and_manifest(monkeypatch, tmp_path):
    source = Source()
    monkeypatch.setattr(cli.MarketDataService, "default", lambda: source)
    archive = tmp_path/cli.ARCHIVE_NAME
    report = tmp_path/"month.json"
    args = ["--year", "2025", "--month", "1", "--mode", "ingest",
            "--archive", str(archive), "--report", str(report),
            "--ack-research-write"]
    assert cli.run(args) == 0
    result = json.loads(report.read_text())
    assert result["d1_source_candidate_days"] == 1
    assert result["days_with_captured_price_rows"] == 1
    assert {(row["timeframe"], row["rows"]) for row in result["inventory"]} == {
        ("H1", 1), ("M15", 1)
    }
    assert not any(row["timeframe"] == "M1" for row in result["inventory"])
    assert source.calls.count(("M1", "2025-01-15", "2025-01-15")) == 1
    # Report cannot be silently replaced.
    with pytest.raises(SystemExit):
        cli.run(args)
    assert len(source.calls) == 4  # resolve + D1 + H1 + M1


def test_d1_empty_cannot_be_falsely_certified(monkeypatch, tmp_path):
    class Empty(Source):
        def candles_for_instrument(self, instrument, *, timeframe,
                                   from_date, till_date, completed_only):
            if timeframe == "D1":
                return CandleSeries(instrument, "D1", ())
            return super().candles_for_instrument(instrument, timeframe=timeframe,
                                                from_date=from_date, till_date=till_date,
                                                completed_only=completed_only)
    monkeypatch.setattr(cli.MarketDataService, "default", lambda: Empty())
    with pytest.raises(ValueError, match="no native D1"):
        cli.run(["--year", "2025", "--month", "1", "--mode", "ingest",
                 "--archive", str(tmp_path/cli.ARCHIVE_NAME),
                 "--report", str(tmp_path/"manifest.json"), "--ack-research-write"])
