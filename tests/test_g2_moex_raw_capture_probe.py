"""MOEX source acquisition boundary tests using a fake governed provider; no network."""
from datetime import datetime,timezone,timedelta
import json
from dataclasses import dataclass
import pytest
from scripts.g2_moex_raw_capture_probe import capture,ISS_PATH

UTC=timezone.utc
DAY=datetime(2026,10,10,12,0,tzinfo=UTC)

@dataclass
class Response:
    body:bytes
    status_code:int=200
    headers:dict=None
    def __post_init__(self):self.headers=self.headers or {'Date':'Sat, 10 Oct 2026 12:00:00 GMT'}

class FakeGovernor:
    def __init__(self,response):self.response=response;self.calls=[]
    def _request(self,path,params):self.calls.append((path,params));return self.response

def body(rows=None):
    rows=rows if rows is not None else [['101.0','2026-10-09 10:00:00','2026-10-09 18:45:00']]
    return json.dumps({'candles':{'columns':['close','begin','end'],'data':rows}}).encode()

def times(start=DAY,end=DAY+timedelta(seconds=2)):
    stack=iter([start,end]);return lambda:next(stack)

def test_real_bytes_sha_and_observed_clock_saved(tmp_path):
    provider=FakeGovernor(Response(body()))
    x=capture(provider,tmp_path/'new',clock=times())
    assert len(provider.calls)==1 and provider.calls[0][0]==ISS_PATH
    assert provider.calls[0][1]['till']=='2026-10-09'
    assert x['raw_byte_count']==len(body()) and x['completed_rows_in_response']==1
    assert x['is_real_forecast'] is False and x['strict_pit_proven'] is False
    assert (tmp_path/'new/moex_sber_d1_raw_response.json').read_bytes()==body()
    assert x['observed_at_end_utc']=='2026-10-10T12:00:02Z'

def test_prevent_overwrite(tmp_path):
    target=tmp_path/'new';target.mkdir();(target/'existing').write_text('x')
    with pytest.raises(ValueError,match='overwrite'):capture(FakeGovernor(Response(body())),target)

def test_refuse_forming_future_candle(tmp_path):
    b=body([['101.0','2026-10-10 10:00:00','2026-10-10 18:45:00']])
    with pytest.raises(ValueError,match='forming'):capture(FakeGovernor(Response(b)),tmp_path/'new',clock=times())
    assert not (tmp_path/'new').exists()

def test_refuse_nonmonotonic_or_duplicate_bars(tmp_path):
    b=body([['101','2026-10-09 10:00:00','2026-10-09 18:45:00'],['102','2026-10-09 10:00:00','2026-10-09 18:45:00']])
    with pytest.raises(ValueError,match='duplicate'):capture(FakeGovernor(Response(b)),tmp_path/'new',clock=times())

def test_refuse_partial_paginated_response(tmp_path):
    payload=json.loads(body());payload['candles.cursor']={'columns':['TOTAL'],'data':[[100]]}
    with pytest.raises(ValueError,match='incomplete'):capture(FakeGovernor(Response(json.dumps(payload).encode())),tmp_path/'new',clock=times())

def test_refuse_clock_reversal(tmp_path):
    with pytest.raises(ValueError,match='reversed'):capture(FakeGovernor(Response(body())),tmp_path/'new',clock=times(DAY+timedelta(seconds=2),DAY))

def test_refuse_empty_response(tmp_path):
    with pytest.raises(ValueError,match='required'):capture(FakeGovernor(Response(b'{"candles":{"columns":[],"data":[]}}')),tmp_path/'new',clock=times())

def test_timezone_naive_clock_fail_closed(tmp_path):
    from datetime import datetime
    with pytest.raises(ValueError,match='timezone aware'):
        capture(FakeGovernor(Response(body())),tmp_path/'new',clock=lambda:datetime(2026,10,10,12,0))

def test_clock_after_provider_cannot_be_naive(tmp_path):
    from datetime import datetime
    clock=iter([DAY, datetime(2026,10,10,12,0)])
    with pytest.raises(ValueError,match='timezone aware'):
        capture(FakeGovernor(Response(body())),tmp_path/'new',clock=lambda:next(clock))
