import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.domain.market import Candle, CandleSeries, Instrument
from scripts.g2_imoex_merge_research_months import (
    FILENAME, checksum_file, expected_months, merge,
)


def test_expected_monthly_coverage_fixed_and_contains_no_future():
    months = expected_months()
    assert len(months) == 22
    assert months[0] == "2025-01" and months[-1] == "2026-10"
    assert "2024-12" not in months and "2026-11" not in months


def single_source(tmp_path):
    month = "2025-01"
    part = tmp_path/f"imoex-intraday-{month}-duckdb"
    part.mkdir()
    store = ResearchIntradayArchive(str(part/FILENAME))
    instrument = Instrument(
        symbol="IMOEX", secid="IMOEX", board="SNDX",
        engine="stock", market="index", asset_class="index",
    )
    start = datetime.fromisoformat("2025-01-15T10:00:00+03:00")
    for tf, duration in (("H1", 3599), ("M15", 899)):
        candle = Candle(
            open=3000., high=3010., low=2990., close=3005.,
            volume=5., value=5000., begin=start.isoformat(),
            end=(start+timedelta(seconds=duration)).isoformat(), completed=True
        )
        store.ingest(CandleSeries(instrument, tf, (candle,)))
    store.mark_captured(instrument, "2025-01-15",
                        h1_rows=1, m15_rows=1, raw_m1_rows=15)
    inventory = store.inventory()
    store.close()
    report = {
        "schema":"G2_IMOEX_MONTHLY_ARCHIVE_V1",
        "symbol":"IMOEX","secid":"IMOEX",
        "from_date":"2025-01-01","till_date":"2025-01-31",
        "d1_source_candidate_days":1,
        "days_with_captured_price_rows":1,"days_missing_source":0,
        "days":[{"date":"2025-01-15","state":"CAPTURED_SOURCE_RESPONSE"}],
        "inventory":inventory
    }
    p=part/"IMOEX_2025-01_coverage.json"
    p.write_text(json.dumps(report))
    checksums = part/"IMOEX_2025-01_SHA256SUMS.txt"
    checksums.write_text(
        f"{checksum_file(p)}  {p.name}\n"
        f"{checksum_file(part/FILENAME)}  {FILENAME}\n"
    )
    return part


def test_single_month_merge_proves_source_sha_integrity_and_immutable_target(tmp_path):
    part = single_source(tmp_path)
    output = tmp_path/"out"
    output.mkdir()
    target=output/FILENAME
    report=output/"combined.json"
    result=merge(tmp_path, target, report,months=("2025-01",))
    assert result["total"]["source_days"] == 1
    assert result["total"]["H1"] == 1
    assert result["total"]["M15"] == 1
    assert result["PIT_certified"] is False
    store=ResearchIntradayArchive(str(target))
    assert sum(x["rows"] for x in store.inventory()) == 2
    store.close()
    with pytest.raises(ValueError,match="overwrite"):
        merge(tmp_path,target,report,months=("2025-01",))


def test_modified_source_rejected_before_target_created(tmp_path):
    part=single_source(tmp_path)
    with (part/"IMOEX_2025-01_coverage.json").open("a") as fd:
        fd.write(" ")
    out=tmp_path/"out"
    out.mkdir()
    with pytest.raises(ValueError, match="source SHA256 mismatch"):
        merge(tmp_path,out/FILENAME,out/"merged.json",months=("2025-01",))
    assert not (out/FILENAME).exists()
