"""Offline source-to-immutable-forecast contract tests; synthetic fixture only."""
from datetime import datetime,timedelta,timezone
import hashlib,json
from pathlib import Path
import pytest

from birzha.domain.market import Instrument
from scripts.g2_freeze_real_forecast_from_source_receipts import (
  contiguous_m15,assemble_snapshot,freeze_from_receipts,
)
from birzha.domain.market import Candle,CandleSeries

NOW=datetime(2026,10,10,15,0,20,tzinfo=timezone.utc)
SEEN="2026-10-10T15:00:05Z"
COLS=["open","close","high","low","value","volume","begin","end"]


def test_incomplete_m15_group_never_compressed():
    ins=Instrument(symbol="SBER",secid="SBER",engine="stock",market="shares",board="TQBR",asset_class="equity")
    start=datetime(2026,10,9,10,0,tzinfo=timezone.utc)
    cs=[]
    for j in range(30):
        if j==7:continue
        t=start+timedelta(minutes=j)
        cs.append(Candle(open=100.,close=101.,high=102.,low=99.,value=1.,volume=1.,
              begin=t.isoformat(),end=(t+timedelta(seconds=59)).isoformat()))
    series,n=contiguous_m15(CandleSeries(instrument=ins,timeframe="M1",candles=tuple(cs)))
    assert series.count==1 and n==1
    assert series.candles[0].begin.endswith("+00:00")


def _raw(rows):
    return json.dumps({"candles":{"columns":COLS,"data":rows}},
        sort_keys=True,separators=(",",":")).encode()


def _row(dt,base=100.0):
    price=base+((dt.day+dt.hour+dt.minute)%17)*0.1
    return [price,price+0.05,price+0.25,price-0.25,1000.0,10.0,
            dt.strftime("%Y-%m-%d %H:%M:%S"),
            (dt+timedelta(seconds=59)).strftime("%Y-%m-%d %H:%M:%S")]


def source_fixture(root):
    """Fabricated exchange rows, never assert this is genuine MOEX evidence."""
    root.mkdir(parents=True)
    ins=Instrument(symbol="SBER",secid="SBER",engine="stock",market="shares",board="TQBR",asset_class="equity")
    starts={
      "D1":[datetime(2026,10,9,10,0)-timedelta(days=j) for j in reversed(range(60))],
      "H1":[datetime(2026,10,9,10,0)-timedelta(hours=j) for j in reversed(range(60))],
      "M1":[datetime(2026,10,9,10,0)+timedelta(minutes=j) for j in range(750)],
    }
    tfs={};filelist=[]
    for tf,minutes in starts.items():
        rows=[_row(dt,100.0+i/200) for i,dt in enumerate(minutes)]
        name=f"SBER_SBER_{tf}_0.json"
        body=_raw(rows)
        (root/name).write_bytes(body)
        h=hashlib.sha256(body).hexdigest()
        filelist.append({"name":name,"sha256":h})
        tfs[tf]={"timeframe":tf,"exact_secid":"SBER","total_completed":len(rows),
          "pages":[{"path":name,"sha256":h,"row_count":len(rows),
            "observed_start_utc":"2026-10-10T15:00:00Z",
            "observed_end_utc":SEEN}]}
    entry={"market":"SBER","resolved_secid":"SBER",
      "status":"SOURCE_CAPTURE_COMPLETE","instrument":ins.to_dict(),"timeframes":tfs}
    manifest={"schema":"G2_SIX_MARKET_LIVE_SOURCE_RECEIPT_V1",
      "origin":"REAL_MOEX_PUBLIC_ISS_CAPTURE","requested_markets":["SBER"],
      "failed_markets":[],"instruments":[entry],"all_payload_files":filelist}
    (root/"manifest.json").write_text(json.dumps(manifest))
    return entry


def test_frozen_forecast_snapshot_t0_is_actual_receipt_after_source(tmp_path):
    src=tmp_path/"source";entry=source_fixture(src)
    # The fixture is synthetic; no independently attested source.
    snapshot,bundle,observed,last,counts,partial=assemble_snapshot(entry,src,clock=lambda:NOW)
    assert all(counts[tf]>=50 for tf in ("D1","H1","M15"))
    assert snapshot.as_of=="2026-10-10T15:00:20Z"
    assert snapshot.data_quality=="DEGRADED"
    assert "source_raw_bundle_sha256=" in " ".join(snapshot.warnings)
    assert last.startswith("2026-10-09") and observed==SEEN
    assert bundle and partial>=0


def test_stale_source_must_not_be_backdated(tmp_path):
    src=tmp_path/"source";entry=source_fixture(src)
    with pytest.raises(ValueError,match="immediately"):
        assemble_snapshot(entry,src,clock=lambda:NOW+timedelta(minutes=6))


def test_original_page_sha_tamper_fails_closed(tmp_path):
    src=tmp_path/"source";entry=source_fixture(src)
    (src/"SBER_SBER_M1_0.json").write_bytes(b"forged")
    with pytest.raises(ValueError,match="SHA mismatch"):
        assemble_snapshot(entry,src,clock=lambda:NOW)


def test_end_to_end_real_contract_in_disposable_duckdb(tmp_path):
    pytest.importorskip("duckdb")
    src=tmp_path/"source";source_fixture(src)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir()}
    out=tmp_path/"staged"
    result=freeze_from_receipts(src,out,clock=lambda:NOW)
    assert result["forecasts_frozen"]==1
    assert result["strict_ex_ante_proof"] is False
    assert result["independent_provider_attestation"] is False
    assert result["markets"][0]["status"]=="FROZEN_REAL_SOURCE_BASELINE"
    assert result["markets"][0]["source_counts"]["M15"]>=50
    from birzha.storage.forecast_journal import DuckDBForecastJournal
    from birzha.storage.outcome_journal import DuckDBOutcomeJournal
    journal=DuckDBForecastJournal(str(out/"SBER/forecast.duckdb"))
    fid=result["markets"][0]["forecast_id"]
    assert journal.get(fid).created_at_t0=="2026-10-10T15:00:20Z"
    assert journal.get(fid).snapshot_id==result["markets"][0]["snapshot_id"]
    journal.close()
    oj=DuckDBOutcomeJournal(str(out/"SBER/outcome.duckdb"))
    assert oj.list_for_forecast(fid)==[]
    oj.close()
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in src.iterdir()}==before


def test_no_fabricated_outcome_with_insufficient_minutes(tmp_path):
    src=tmp_path/"source";entry=source_fixture(src)
    obj=json.loads((src/"SBER_SBER_M1_0.json").read_bytes())
    obj["candles"]["data"]=obj["candles"]["data"][:60]
    content=_raw(obj["candles"]["data"])
    (src/"SBER_SBER_M1_0.json").write_bytes(content)
    entry["timeframes"]["M1"]["total_completed"]=60
    entry["timeframes"]["M1"]["pages"][0]["row_count"]=60
    entry["timeframes"]["M1"]["pages"][0]["sha256"]=hashlib.sha256(content).hexdigest()
    with pytest.raises(ValueError,match="insufficient actual completed bars"):
        assemble_snapshot(entry,src,clock=lambda:NOW)
