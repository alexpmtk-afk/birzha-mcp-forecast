"""Q-087: exact original MOEX M1 bytes -> M15 offline replay. Synthetic."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from birzha.application.g2_m1_m15_source_replay import (
    OriginalM1Page, SourceReplayRefused, replay_completed_m15,
)
from birzha.domain.market import Candle, Instrument

MSK=timezone(timedelta(hours=3))
START=datetime(2026,10,9,10,0,tzinfo=MSK)
RECEIVED="2026-10-09T07:16:05Z"
END="2026-10-09T07:14:59Z"
URL="https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/SBER/candles.json?interval=1&start=0&from=2026-10-09&till=2026-10-09"


def inst():
    return Instrument(symbol="SBER",secid="SBER",board="TQBR",
                      engine="stock",market="shares",asset_class="equity")


def rows():
    a=[]
    for i in range(15):
        begin=START+timedelta(minutes=i)
        last=begin+timedelta(seconds=59)
        a.append([101.+i/10, 101.05+i/10,102.+i/10,100.+i/10,
                  1000.+i,10.+i,begin.strftime("%Y-%m-%d %H:%M:%S"),
                  last.strftime("%Y-%m-%d %H:%M:%S")])
    return a


def make_page(items=None, *, start=0, total=None, page_size=None, url=None,
              first="2026-10-09T07:16:00Z",last=RECEIVED):
    data={"candles":{"columns":["open","close","high","low","value","volume","begin","end"],
                     "data":rows() if items is None else items}}
    if total is not None:
        data["candles.cursor"]={"columns":["TOTAL","PAGESIZE"],
                                "data":[[total,page_size]]}
    raw=json.dumps(data,ensure_ascii=False,separators=(",",":")).encode()
    return OriginalM1Page(raw_body=raw,raw_sha256=sha256(raw).hexdigest(),
                          request_url=url or URL.replace("start=0",f"start={start}"),
                          http_status=200,content_type="application/json; charset=utf-8",
                          request_started_utc=first,response_received_utc=last,start=start)


def checked(pages=None, **kw):
    return replay_completed_m15(instrument=kw.pop("instrument",inst()),
                                pages=pages if pages is not None else (make_page(),),
                                m15_begin_utc=kw.pop("m15_begin_utc","2026-10-09T07:00:00Z"),
                                evaluated_at_utc=kw.pop("evaluated_at_utc","2026-10-09T07:16:10Z"),
                                **kw)


def candidate():
    m=checked()["m15_from_source"]
    return Candle(open=m["open"],close=m["close"],high=m["high"],low=m["low"],
                  value=m["value"],volume=m["volume"],begin=m["begin"],end=m["end"],
                  completed=True,available_at_confidence="UNKNOWN")


def test_full_raw_bytes_replay_matches_all_ohlcv_without_authentication():
    result=checked(candidate=candidate())
    m=result["m15_from_source"]
    assert m["begin"]=="2026-10-09T07:00:00Z"
    assert m["end"]==END
    assert m["open"]==101. and m["close"]==102.45
    assert m["high"]==103.4 and m["low"]==100.
    assert m["volume"]==255. and m["value"]==15105.
    assert result["original_m1_rows_used"]==15
    assert result["source_bytes_replayed_and_matching"] is True
    assert result["source_transport_independently_authenticated"] is False
    assert result["receipt_clock_independently_attested"] is False
    assert result["level_journal_admission_authorized"] is False
    assert result["forecast_or_trade_authorized"] is False


@pytest.mark.parametrize("variant", [
    "hash","host","scheme","secid","board","market","engine","interval",
    "start_mismatch","status","content_type","future_observation",
    "inverted_clock","naive_clock","overlong_http","query_day",
])
def test_source_claim_and_receipt_tampering_rejected(variant):
    p=make_page()
    if variant=="hash":p=replace(p,raw_sha256="0"*64)
    elif variant=="host":p=replace(p,request_url=p.request_url.replace("iss.moex.com","iss.moex.com.attacker.example"))
    elif variant=="scheme":p=replace(p,request_url=p.request_url.replace("https://","http://"))
    elif variant=="secid":p=replace(p,request_url=p.request_url.replace("/SBER/","/GAZP/"))
    elif variant=="board":p=replace(p,request_url=p.request_url.replace("/TQBR/","/TQTF/"))
    elif variant=="market":p=replace(p,request_url=p.request_url.replace("/shares/","/bonds/"))
    elif variant=="engine":p=replace(p,request_url=p.request_url.replace("/stock/","/futures/"))
    elif variant=="interval":p=replace(p,request_url=p.request_url.replace("interval=1","interval=10"))
    elif variant=="start_mismatch":p=replace(p,request_url=p.request_url.replace("start=0","start=15"))
    elif variant=="status":p=replace(p,http_status=500)
    elif variant=="content_type":p=replace(p,content_type="text/html")
    elif variant=="future_observation":p=replace(p,response_received_utc="2026-10-09T07:25:00Z")
    elif variant=="inverted_clock":p=replace(p,request_started_utc="2026-10-09T07:20:00Z")
    elif variant=="naive_clock":p=replace(p,response_received_utc="2026-10-09T07:16:05")
    elif variant=="overlong_http":p=replace(p,request_started_utc="2026-10-09T07:00:00Z")
    elif variant=="query_day":p=replace(p,request_url=p.request_url.replace("till=2026-10-09","till=2026-10-08"))
    with pytest.raises(SourceReplayRefused):
        checked((p,))


@pytest.mark.parametrize("variant",[
    "missing","duplicate","gap","wrong_duration","future_m1","negative_volume",
    "nan","out_of_order","inconsistent_high","partial","extra","missing_column",
])
def test_incomplete_or_corrupted_m1_original_bytes_rejected(variant):
    r=rows()
    if variant=="missing":r.pop(7)
    elif variant=="duplicate":r[8]=r[7][:]
    elif variant=="gap":r[7][6]="2026-10-09 10:08:00";r[7][7]="2026-10-09 10:08:59"
    elif variant=="wrong_duration":r[7][7]="2026-10-09 10:07:58"
    elif variant=="future_m1":r[14][7]="2026-10-09 10:25:59"
    elif variant=="negative_volume":r[2][5]=-1.
    elif variant=="nan":r[0][0]=None
    elif variant=="out_of_order":r[7],r[8]=r[8],r[7]
    elif variant=="inconsistent_high":r[4][2]=99.
    elif variant=="partial":r=r[:14]
    elif variant=="extra":r.append(r[-1][:])
    elif variant=="missing_column":
        p=make_page(r); body=json.loads(p.raw_body)
        body["candles"]["columns"].remove("volume")
        body["candles"]["data"]=[x[:-1] for x in r]
        raw=json.dumps(body).encode()
        p=replace(p,raw_body=raw,raw_sha256=sha256(raw).hexdigest())
        with pytest.raises(SourceReplayRefused):checked((p,))
        return
    with pytest.raises(SourceReplayRefused):
        checked((make_page(r),))


@pytest.mark.parametrize("field",["open","close","high","low","volume","value"])
def test_claimed_aggregated_m15_mismatch_rejected(field):
    old=candidate()
    with pytest.raises(SourceReplayRefused):
        checked(candidate=replace(old,**{field:getattr(old,field)+1.}))


def test_cursor_pagination_complete_and_truncated_rejected():
    a=rows()
    p0=make_page(a[:8],start=0,total=15,page_size=8)
    p1=make_page(a[8:],start=8,total=15,page_size=8,
                 first="2026-10-09T07:16:05Z",last="2026-10-09T07:16:08Z")
    assert checked((p0,p1),evaluated_at_utc="2026-10-09T07:16:10Z")["original_m1_rows_used"]==15
    with pytest.raises(SourceReplayRefused):
        checked((p0,))
    with pytest.raises(SourceReplayRefused):
        checked((p0,replace(p1,start=9)))


def test_unaligned_unfinished_and_explicit_unknown_candidate():
    with pytest.raises(SourceReplayRefused):
        checked(m15_begin_utc="2026-10-09T07:01:00Z")
    with pytest.raises(SourceReplayRefused):
        checked(evaluated_at_utc="2026-10-09T07:10:00Z")
    assert checked(candidate=candidate())["level_journal_admission_authorized"] is False


def test_duplicate_json_member_refused_even_if_source_sha_is_recomputed():
    p=make_page()
    raw=p.raw_body.replace(b'"candles":',b'"candles":{},"candles":',1)
    with pytest.raises(SourceReplayRefused):
        checked((replace(p,raw_body=raw,raw_sha256=sha256(raw).hexdigest()),))
