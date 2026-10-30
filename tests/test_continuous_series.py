from copy import deepcopy
import pytest
from birzha.application.continuous_series import build_continuous_series

def source():
    def row(secid,day,volume,close):
        return dict(SECID=secid,ASSETCODE="RI",TRADEDATE=day,LASTTRADEDATE="2026-06-18" if secid=="RIM6" else "2026-03-19",VOLUME=volume,CLOSE=close,
                    completed_at=day+"T23:59:59+03:00",available_at=day+"T23:59:59+03:00",source_sha256="a"*64)
    sessions=[
       dict(decision_session="2026-03-17",effective_session="2026-03-18",decision_cutoff="2026-03-18T08:00:00+03:00",
            decision_rows=[row("RIH6","2026-03-17",100,100),row("RIM6","2026-03-17",50,110)]),
       dict(decision_session="2026-03-18",effective_session="2026-03-19",decision_cutoff="2026-03-19T08:00:00+03:00",
            decision_rows=[row("RIH6","2026-03-18",50,100),row("RIM6","2026-03-18",100,110)])]
    bars=[]
    for day,secid,close in [("2026-03-18","RIH6",100),("2026-03-19","RIM6",110)]:
        for tf in ["D1","H1","M15"]:
            bars.append(dict(secid=secid,timeframe=tf,trade_session_date=day,begin=day+"T10:00:00+03:00",
                 end=day+"T10:15:00+03:00",completed=True,open=close,close=close,high=close+2,low=close-2,volume=5,source_sha256="b"*64))
    return dict(root="RI",coordinate="ADDITIVE_BACK",as_of="2026-03-20T08:00:00+03:00",sessions=sessions,bars=bars)

def test_coordinates_preserve_raw_volume_and_input():
    x=source();original=deepcopy(x)
    result=build_continuous_series(x)
    assert x==original
    assert result["forecast_allowed"] is False
    assert result["roll_events"][0]["basis"]==10
    assert all(b["close"]==110 and b["volume"]==5 for b in result["bars"])
    x["coordinate"]="FORWARD_SPREAD"
    f=build_continuous_series(x)
    assert all(b["close"]==100 for b in f["bars"])
    x["coordinate"]="RATIO_BACK"
    assert all(b["close"]==pytest.approx(110) for b in build_continuous_series(x)["bars"])
    x["coordinate"]="RAW_CONTRACT"
    assert {b["close"] for b in build_continuous_series(x)["bars"]}=={100,110}

@pytest.mark.parametrize("fault",["future","late","duplicate","missing_overlap","missing_tf","forming","bad_hash","bad_ohlc","unknown_day","wrong_root"])
def test_refuses_bad_evidence(fault):
    x=source()
    if fault=="future":x["as_of"]="2026-03-18T08:00:00+03:00"
    if fault=="late":x["sessions"][1]["decision_rows"][0]["available_at"]="2026-03-20T00:00:00+03:00"
    if fault=="duplicate":x["bars"].append(deepcopy(x["bars"][0]))
    if fault=="missing_overlap":x["sessions"][1]["decision_rows"].pop(0)
    if fault=="missing_tf":x["bars"].pop()
    if fault=="forming":x["bars"][0]["completed"]=False
    if fault=="bad_hash":x["bars"][0]["source_sha256"]="x"
    if fault=="bad_ohlc":x["bars"][0]["high"]=0
    if fault=="unknown_day":x["bars"][0]["trade_session_date"]="2026-03-01"
    if fault=="wrong_root":x["root"]="RTSI"
    with pytest.raises((ValueError,KeyError)):build_continuous_series(x)

def test_decision_must_precede_target_bar():
    x=source()
    x["sessions"][0]["decision_cutoff"]="2026-03-18T11:00:00+03:00"
    with pytest.raises(ValueError,match="decision cutoff"):build_continuous_series(x)

def test_future_roll_event_does_not_reprice_earlier_cutoff():
    x=source();x["sessions"]=x["sessions"][:1];x["bars"]=x["bars"][:3];x["as_of"]="2026-03-18T23:00:00+03:00"
    r=build_continuous_series(x)
    assert not r["roll_events"] and all(b["close"]==100 for b in r["bars"])
