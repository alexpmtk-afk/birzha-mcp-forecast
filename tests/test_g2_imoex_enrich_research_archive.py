import hashlib
import json

import pytest

from scripts.g2_imoex_enrich_research_archive import _validate_report,upgrade
from scripts.g2_imoex_source_interval_recovery import SOURCE_SHA


def fixture(tmp_path, count=8):
    month="2025-06"
    folder=tmp_path/"imoex-source-exceptions-2025-06"
    folder.mkdir(parents=True)
    row={
       "symbol":"IMOEX","secid":"IMOEX","timeframe":"M15",
       "begin":"2025-06-09T10:00:00+03:00",
       "end":"2025-06-09T10:14:59+03:00",
       "open":3000.0,"high":3010.0,"low":2990.0,"close":3002.0,
       "volume":3.0,"value":9000.0,
       "quality":"SOURCE_M15_WITH_FEWER_THAN_15_M1",
       "source_minute_rows":count,
       "synthetic_price":False,
       "available_at_confidence":"INFERRED",
    }
    report={
       "schema":"IMOEX_SOURCE_INTERVAL_RECOVERY_V1",
       "month":month,
       "original_archive_sha256":SOURCE_SHA,
       "source_days_match_original":True,
       "source_changes_rejected":True,
       "no_invented_prices":True,
       "days":1,
       "native_short_h1":0,
       "partial_m15_from_source":1,
       "unresolved_h1_reported":0,
       "unresolved_m15_reported":0,
       "rows":[{
          "day":"2025-06-09",
          "recovered_h1":0,"recovered_m15":1,
          "unresolved_h1_reported":0,
          "unresolved_m15_reported":0,
          "supplemental":[row],
       }],
    }
    path=folder/"IMOEX_2025-06_AUTHENTIC_SOURCE_EXCEPTIONS.json"
    path.write_text(json.dumps(report,sort_keys=True))
    sums=folder/"IMOEX_2025-06_RECOVERY_SHA256SUMS.txt"
    sums.write_text(hashlib.sha256(path.read_bytes()).hexdigest()+"  "+path.name+"\n")
    return folder,path,sums


def test_original_source_exception_parses_without_price_interpolation(tmp_path):
    folder,_,_=fixture(tmp_path)
    days,rows,counts=_validate_report(folder,"2025-06")
    assert days=={"2025-06-09"}
    assert len(rows)==1 and rows[0][13]==8
    assert counts==(0,1,0,0)


def test_wrong_shasum_fails_closed(tmp_path):
    folder,path,_=fixture(tmp_path)
    path.write_text(path.read_text()+" ")
    with pytest.raises(ValueError,match="tampered"):
        _validate_report(folder,"2025-06")


def test_15_of_15_or_zero_minutes_cannot_be_misclassified_as_partial(tmp_path):
    for count in (0,15,16):
        folder,_,_=fixture(tmp_path/f"x{count}",count=count)
        with pytest.raises(ValueError,match="1..14"):
            _validate_report(folder,"2025-06")


def test_never_modify_existing_target_or_preexisting_archive(tmp_path):
    source=tmp_path/"original.duckdb"
    source.write_bytes(b"immutable")
    target=tmp_path/"output.duckdb"
    target.write_bytes(b"existing")
    with pytest.raises(ValueError,match="separate clean"):
        upgrade(source,tmp_path/"missing",target,tmp_path/"report.json")
    assert target.read_bytes()==b"existing"
    assert source.read_bytes()==b"immutable"
