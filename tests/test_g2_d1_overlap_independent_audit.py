"""SHA-pinned retrospective audit and unchanged no-clobber Development outputs."""
from datetime import date,timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.g2_d1_overlap_independent_audit import (
    _nonoverlap, _verify_manifest, run, AUDIT_SCHEMA,
)
from scripts.g2_first_forecast_development import (
    _write_immutable, load_rows, predict, run as first_run,
    EXPERIMENT_VERSION, FEATURE_VERSION, stable_json,
)
from test_g2_first_forecast_development import row


def fixture(tmp_path, *, count=31):
    inp=tmp_path/"development.jsonl"
    records=[row(n,d5=1 if n%3 else -1) for n in range(count)]
    inp.write_bytes((''.join(stable_json(r)+"\n" for r in records)).encode())
    manifest={
        "schema":"G2_SIX_MARKET_D1_DEVELOPMENT_DATASET_V1",
        "scope":"RECONSTRUCTED_RESEARCH_ONLY",
        "strict_historical_as_known_at_T0":False,
        "markets":["SBER","Si","BR","GOLD","IMOEX","RTSI"],
        "development_from":"2021-01-01",
        "development_till":"2022-12-31",
        "feature_version":FEATURE_VERSION,
        "source_sha256_before_after":"a"*64,
        "accepted_rows":count,
        "accepted_rows_sha256":sha256(inp.read_bytes()).hexdigest(),
    }
    m=tmp_path/"manifest.json"
    m.write_text(stable_json(manifest)+"\n",encoding="utf-8")
    return inp,m


def test_q124_bound_dataset_and_manifest_refuse_wrong_sha_and_vintage(tmp_path):
    inp,m=fixture(tmp_path)
    assert _verify_manifest(inp,m)[1]==sha256(inp.read_bytes()).hexdigest()
    bad=json.loads(m.read_text())
    bad["accepted_rows_sha256"]="0"*64
    m.write_text(stable_json(bad))
    with pytest.raises(ValueError,match="manifest do not agree"):
        run(inp,m,tmp_path/"report.json")
    bad["accepted_rows_sha256"]=sha256(inp.read_bytes()).hexdigest()
    bad["strict_historical_as_known_at_T0"]=True
    m.write_text(stable_json(bad))
    with pytest.raises(ValueError,match="manifest do not agree"):
        run(inp,m,tmp_path/"report.json")
    assert not (tmp_path/"report.json").exists()


def test_q125_q126_old_date_nonoverlap_has_lower_sample_and_year_only(tmp_path):
    inp,m=fixture(tmp_path)
    result=run(inp,m,tmp_path/"report.json")
    assert result["schema"]==AUDIT_SCHEMA
    assert result["candidate_feature_rows"]==31
    assert result["Forecast_V1_measured"] is False
    assert result["untouched_2023_2024_holdout_opened"] is False
    assert result["release_allowed"] is False
    assert set(result["by_origin_year"])=={"2021","2022"}
    assert all(result["by_origin_year"]["2022"][str(h)]["candidates"]==0
               for h in (5,10,20))
    for h in (5,10,20):
        overlap=result["overlapping"][str(h)]
        clean=result["nonoverlapping_greedy_per_market"][str(h)]
        assert 0<clean["scored"]<overlap["scored"]
        assert overlap["candidates"]==31
        assert clean["d5_sign_hits"]<=clean["scored"]
        assert clean["always_up_hits_on_same_rows"]<=clean["scored"]


def test_q125_report_is_identical_on_repeat_and_refuses_modified_source(tmp_path):
    inp,m=fixture(tmp_path)
    target=tmp_path/"audit.json"
    result=run(inp,m,target)
    orig=target.read_bytes()
    assert run(inp,m,target)==result
    assert target.read_bytes()==orig
    target.write_bytes(b"FORGED\n")
    with pytest.raises(FileExistsError,match="unlike"):
        run(inp,m,target)
    target.write_bytes(orig)
    original=inp.read_bytes()
    inp.write_bytes(original+b"\n")
    with pytest.raises(ValueError,match="manifest do not agree"):
        run(inp,m,target)
    assert target.read_bytes()==orig


def test_q127_first_forecast_original_pred_artifacts_never_clobbered(tmp_path):
    inp,_=fixture(tmp_path)
    out=tmp_path/"first"
    record=first_run(inp,out)
    pred=out/"g2_first_forecast_predictions.jsonl"
    outcome=out/"g2_first_forecast_outcomes.jsonl"
    summary=out/"g2_first_forecast_summary.json"
    old=(pred.read_bytes(),outcome.read_bytes(),summary.read_bytes())
    assert first_run(inp,out)==record
    assert (pred.read_bytes(),outcome.read_bytes(),summary.read_bytes())==old
    outcome.write_bytes(b"UNLIKE OUTCOME")
    with pytest.raises(FileExistsError,match="unlike"):
        first_run(inp,out)
    assert pred.read_bytes()==old[0]
    assert outcome.read_bytes()==b"UNLIKE OUTCOME"


def test_q127_symlink_target_is_never_frozen_as_valid_research_file(tmp_path):
    real=tmp_path/"outside"
    real.write_bytes(b"same")
    target=tmp_path/"artifact"
    target.symlink_to(real)
    with pytest.raises(FileExistsError,match="symlink"):
        _write_immutable(target,b"same")
    assert real.read_bytes()==b"same"


def test_q128_bool_feature_never_becomes_directional_prediction(tmp_path):
    r=row(0)
    r["features"]["features"]["d5_atr"]={"status":"AVAILABLE","value":True}
    inp=tmp_path/"rows.jsonl";inp.write_text(stable_json(r)+"\n")
    records=load_rows(inp)
    assert all(x["prediction"]=="ABSTAIN" for x in predict(records))


def test_q128_python_optimized_mode_still_refuses_fake_receipt(tmp_path):
    inp,m=fixture(tmp_path)
    content=inp.read_text()
    first=json.loads(content.splitlines()[0])
    first["historical_first_receipt"]="AUTHENTICATED_HISTORICAL_T0"
    inp.write_text(stable_json(first)+"\n"+'\n'.join(content.splitlines()[1:])+"\n")
    env=os.environ.copy()
    command=[
        sys.executable,"-O","-c",
        "import sys;from scripts.g2_first_forecast_development import load_rows;"
        "load_rows(__import__('pathlib').Path(sys.argv[1]))",
        str(inp),
    ]
    p=subprocess.run(command,capture_output=True,text=True,
                     env=env,timeout=20)
    assert p.returncode!=0
    assert "false_first_receipt" in (p.stdout+p.stderr)


def test_q128_future_index_duplicate_calendar_evidence_refuses_even_optimized(tmp_path):
    from scripts.g2_first_forecast_development import future_index
    a=row(0)
    b=row(5)
    # Different future target row but same immutable expected origin key.
    b["expected_sessions"]=list(a["expected_sessions"])
    with pytest.raises(ValueError,match="ambiguous_calendar_evidence"):
        future_index([a,b])
