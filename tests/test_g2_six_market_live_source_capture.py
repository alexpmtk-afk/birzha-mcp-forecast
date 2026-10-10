"""Offline regression of six-market source capture; no network, no HOME."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import pytest

from scripts.g2_six_market_live_source_capture import (
    MARKETS, TIMEFRAMES, RECEIPT_VERSION, MAX_PAGES,
    capture_all, collect_one, _parse_page,
)

FIXED=datetime(2026,10,10,14,50,tzinfo=timezone.utc)

@dataclass(frozen=True)
class Instrument:
    symbol:str
    secid:str
    engine:str
    market:str
    board:str
    def to_dict(self):return self.__dict__.copy()

class MarketData:
    def __init__(self, missing=None):self.missing=missing
    def resolve(self,market):
        if market==self.missing:raise LookupError("not listed")
        if market in ("Si","BR","GOLD"):
            secid={"Si":"SiZ6","BR":"BRZ6","GOLD":"GDZ6"}[market]
            return Instrument(market,secid,"futures","forts","RFUD")
        if market in ("IMOEX","RTSI"):
            return Instrument(market,market,"stock","index","SNDX")
        return Instrument(market,market,"stock","shares","TQBR")

@dataclass
class Response:
    body:bytes
    status_code:int=200
    headers:dict=None
    def __post_init__(self):self.headers=self.headers or {"Date":"Sat, 10 Oct 2026 14:50:00 GMT"}

def page(rows=None, cursor=None):
    rows=rows if rows is not None else [[100.0,101.0,102.0,99.0,None,None,"2026-10-09 10:00:00","2026-10-09 18:50:00"]]
    v={"candles":{"columns":["open","close","high","low","value","volume","begin","end"],"data":rows}}
    if cursor:v["candles.cursor"]={"columns":["TOTAL","PAGESIZE"],"data":[cursor]}
    return Response(json.dumps(v).encode())

class Provider:
    def __init__(self, broken_tf=None):self.calls=[];self.broken_tf=broken_tf
    def _request(self,path,params):
        self.calls.append((path,params))
        if self.broken_tf=="M1" and params["interval"]==1:return page([])
        return page()

def clock():return FIXED

def test_exact_six_market_complete_and_raw_manifest(tmp_path):
    p=Provider()
    m=capture_all(p,MarketData(),tmp_path/"six",clock=clock)
    assert m["schema"]==RECEIPT_VERSION and m["verified_complete_market_count"]==6
    assert m["failed_markets"]==[] and m["complete_pages"]==18
    assert len(p.calls)==18
    assert [x["resolved_secid"] for x in m["instruments"]]==["SBER","SiZ6","BRZ6","GDZ6","IMOEX","RTSI"]
    assert all(set(x["timeframes"])==set(TIMEFRAMES) for x in m["instruments"])
    assert m["is_forecast"] is False and m["strict_external_timestamp_attested"] is False
    assert (tmp_path/"six/manifest.json").exists()
    assert all((tmp_path/"six"/f["name"]).exists() for f in m["all_payload_files"])

def test_unresolved_market_fails_closed_without_other_markets_lost(tmp_path):
    m=capture_all(Provider(),MarketData(missing="IMOEX"),tmp_path/"six",clock=clock)
    assert m["verified_complete_market_count"]==5
    assert m["failed_markets"][0]["market"]=="IMOEX"
    assert m["failed_markets"][0]["status"]=="SOURCE_CAPTURE_INCOMPLETE"

def test_missing_m1_refuses_to_certify_market(tmp_path):
    m=capture_all(Provider(broken_tf="M1"),MarketData(),tmp_path/"six",clock=clock,markets=("SBER",))
    assert not m["instruments"] and len(m["failed_markets"])==1

def test_futures_root_never_masquerades_as_contract(tmp_path):
    class Broken(MarketData):
        def resolve(self,market):
            return Instrument("GOLD","GOLD","futures","forts","RFUD")
    m=capture_all(Provider(),Broken(),tmp_path/"six",clock=clock,markets=("GOLD",))
    assert not m["instruments"] and "unresolved futures" in m["failed_markets"][0]["detail"]

def test_fresh_output_dir_required(tmp_path):
    p=tmp_path/"used";p.mkdir();(p/"manifest.json").write_text("{}")
    with pytest.raises(ValueError,match="fresh"):
        capture_all(Provider(),MarketData(),p,clock=clock)

def test_refuse_unfinished_or_future(tmp_path):
    bad=page([[100,101,102,99,None,None,"2026-10-09 10:00:00","2026-10-11 18:50:00"]])
    with pytest.raises(ValueError,match="outside pinned"):
        _parse_page(bad,earliest=FIXED.date()-__import__('datetime').timedelta(days=1),latest=FIXED.date(),observed=FIXED)

def test_refuse_source_paginated_without_cursor(tmp_path):
    bunch=[[100,101,102,99,None,None,"2026-10-09 10:00:00","2026-10-09 18:50:00"]]*500
    with pytest.raises(ValueError,match="pagination"):
        _parse_page(page(bunch),earliest=FIXED.date()-__import__('datetime').timedelta(days=1),latest=FIXED.date(),observed=FIXED)

def test_refuse_duplicate_candle_end_in_multi_page(tmp_path):
    p=Provider()
    class Multipage(Provider):
        def _request(self,path,params):
            self.calls.append((path,params))
            if params["start"]==0:return page(cursor=[2,1])
            return page(cursor=[2,1])
    with pytest.raises(ValueError,match="duplicate"):
        collect_one(Multipage(),MarketData().resolve("SBER"),"D1",FIXED,clock,tmp_path)

def test_nan_or_bad_high_low_is_rejected():
    invalid=page([[100,101,100,99,None,None,"2026-10-09 10:00:00","2026-10-09 18:50:00"]])
    with pytest.raises(ValueError,match="high/low"):
        _parse_page(invalid,earliest=FIXED.date()-__import__("datetime").timedelta(days=1),latest=FIXED.date(),observed=FIXED)
