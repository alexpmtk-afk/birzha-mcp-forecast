"""Synthetic negative tests for the read-only post-maturity readiness audit."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile
import pytest

from scripts.g2_post_maturity_outcome_readiness import readiness, verify_frozen

MARKETS=("SBER","Si","BR","GOLD","IMOEX","RTSI")


def h(data):
    return sha256(data).hexdigest()


def canon(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def fixture(path, *, bad_raw=False, late_receipt=False):
    raw_list=[]
    contents={}
    for market in MARKETS:
        for i in range(5):
            name=f"{market}_real_page_{i}.json"
            value=(f'{{"capture":"SYNTHETIC_ONLY","market":"{market}","page":{i}}}').encode()
            contents["g2-raw-sources/"+name]=value
            raw_list.append({"name":name,"sha256":h(value)})
    if bad_raw:
        contents["g2-raw-sources/SBER_real_page_0.json"]=b"changed after original sha"
    source={"failed_markets":[],"all_payload_files":raw_list}
    raw_manifest=canon(source)
    contents["g2-raw-sources/manifest.json"]=raw_manifest
    rows=[]
    for market in MARKETS:
        fid="fcst_fixture_"+market
        t0="2026-10-10T15:15:48Z"
        forecast={
          "forecast_id":fid,"snapshot_id":"snap_fixture_"+market,
          "symbol":market,"secid":market+"Z6" if market in ("Si","BR","GOLD") else market,
          "created_at_t0":t0,"reference_price":100,
          "horizons":[{"sessions":n,"direction":"UP"} for n in (5,10,20)]}
        forecast_sha=h(canon(forecast))
        src_sha=h(market.encode())
        rec={"forecast_id":fid,"snapshot_id":forecast["snapshot_id"],
          "forecast_sha256":forecast_sha,"source_sha256":src_sha,
          "captured_at":"2026-10-10T15:15:49Z",
          "source_observed_at":"2026-10-10T15:15:42Z",
          "latest_completed_event_end":"2026-10-09T20:00:00Z"}
        if late_receipt and market=="Si":
            rec["source_observed_at"]="2026-10-10T15:15:52Z"
        contents[f"g2-frozen-forecasts/{market}/forecast_record.json"]=canon(forecast)
        contents[f"g2-frozen-forecasts/{market}/capture_receipt.json"]=canon(rec)
        rows.append({"market":market,"secid":forecast["secid"],"forecast_id":fid,
          "snapshot_id":forecast["snapshot_id"],"forecast_record_sha256":forecast_sha,
          "input_bundle_sha256":src_sha,"decision_t0":t0,
          "status":"FROZEN_REAL_SOURCE_BASELINE"})
    top={"source_manifest_sha256":h(raw_manifest),
         "source_failures":[],"forecasts_frozen":6,
         "expected_source_markets":list(MARKETS),"markets":rows}
    contents["g2-frozen-forecasts/forecast_manifest.json"]=canon(top)
    with ZipFile(path,"w") as z:
        for name, value in contents.items():
            z.writestr(name,value)
    return path


def test_six_forecasts_eighteen_horizons_remain_pending(tmp_path):
    p=fixture(tmp_path/"frozen.zip")
    outcome=readiness(p)
    assert outcome["forecast_count"]==6 and outcome["horizon_count"]==18
    assert outcome["status_counts"]=={"PENDING_FUTURE_SESSIONS":18}
    assert outcome["canonical_outcomes_appended"]==0
    assert outcome["quality_metric_available"] is False
    assert all(not row["append_to_canonical_journal"] for row in outcome["outcomes"])


def test_changed_original_source_sha_rejected(tmp_path):
    p=fixture(tmp_path/"bad.zip",bad_raw=True)
    with pytest.raises(ValueError, match="raw provider source hash mismatch"):
        readiness(p)


def test_noncausal_source_timestamp_fails_closed(tmp_path):
    p=fixture(tmp_path/"future.zip",late_receipt=True)
    with pytest.raises(ValueError,match="noncausal"):
        readiness(p)


def test_missing_archive_fails_closed(tmp_path):
    with pytest.raises(ValueError,match="missing"):
        readiness(tmp_path/"does-not-exist.zip")


def test_frozen_manifest_duplicate_market_rejected(tmp_path):
    p=fixture(tmp_path/"dups.zip")
    with ZipFile(p,"r") as z:
        content={n:z.read(n) for n in z.namelist()}
    m=json.loads(content["g2-frozen-forecasts/forecast_manifest.json"])
    m["markets"][1]["market"]="SBER"
    content["g2-frozen-forecasts/forecast_manifest.json"]=canon(m)
    with ZipFile(p,"w") as z:
        for k,v in content.items():
            z.writestr(k,v)
    with pytest.raises(ValueError,match="duplicate/unknown"):
        readiness(p)


def test_future_zip_wrong_contract_cannot_mature_any_horizon(tmp_path):
    p=fixture(tmp_path/"frozen.zip")
    q=tmp_path/"future.zip"
    manifest={"instruments":[],"failed_markets":[]}
    with ZipFile(q,"w") as z:
        z.writestr("g2-raw-sources/manifest.json",canon(manifest))
    result=readiness(p,future_zip=q)
    assert result["status_counts"]=={"FUTURE_EXACT_CONTRACT_NOT_AVAILABLE":18}
    assert result["canonical_outcomes_appended"]==0
