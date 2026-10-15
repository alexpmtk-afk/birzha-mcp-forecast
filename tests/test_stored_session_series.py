import asyncio
import json
import os
from pathlib import Path
import sys
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from birzha.application.historical_data import HistoricalDataService
from birzha.application.stored_session_series import stored_session_series, StoredSessionSeriesError
from birzha.domain.market import Instrument, Candle, CandleSeries
from birzha.storage.historical_store import DuckDBHistoricalCandleStore


def instrument(secid="SBER"):
    return Instrument(symbol=secid,secid=secid,board="TQBR",engine="stock",market="shares",asset_class="equity")


def candles(tf):
    result=[]
    for day,price in [("2026-10-09",100.25),("2026-10-12",130.75)]:
        start="00:00:00" if tf=="D1" else "10:00:00"
        end={"D1":"23:49:59","H1":"10:59:59","M15":"10:14:59"}[tf]
        result.append(Candle(price,price+1,price+2,price-1,None,None,
            day+" "+start,day+" "+end,completed=(day=="2026-10-09"),
            available_at=None,available_at_confidence="UNKNOWN",source="SYNTHETIC_ONLY"))
    return result


def seeded(tf="D1"):
    db=DuckDBHistoricalCandleStore(":memory:")
    bars=candles(tf)
    # Separate sessions arrive in reverse order, then one session is replayed.
    for c in (bars[1],bars[0],bars[0]):db.upsert_series(CandleSeries(instrument(),tf,(c,)))
    return HistoricalDataService(market_data=object(),store=db)


@pytest.mark.parametrize("tf",["D1","H1","M15"])
def test_existing_store_joins_sessions_without_price_time_or_null_changes(tf):
    svc=seeded(tf)
    try:
        raw=svc.store.read(instrument(),tf,"2026-10-09","2026-10-12").to_dict()
        before=svc.store._connection.execute("SELECT * FROM historical_candles ORDER BY begin").fetchall()
        got=stored_session_series(svc,secid="SBER",timeframe=tf,from_date="2026-10-09",till_date="2026-10-12")
        again=stored_session_series(svc,secid="SBER",timeframe=tf,from_date="2026-10-09",till_date="2026-10-12")
        assert got==again
        assert got["candles"]==raw["candles"] and got["count"]==2
        assert [r["open"] for r in got["candles"]]==[100.25,130.75]
        assert all(r["volume"] is None and r["available_at"] is None for r in got["candles"])
        assert got["completed_count"]==got["incomplete_count"]==1
        assert got["price_adjustment"]=="NONE" and got["inter_session_gaps"]=="PRESERVED"
        assert got["session_calendar_status"]=="UNVERIFIED"
        assert got["forecast_admission"]=="NOT_GRANTED"
        assert before==svc.store._connection.execute("SELECT * FROM historical_candles ORDER BY begin").fetchall()
    finally:svc.store.close()


@pytest.mark.parametrize("kwargs,reason",[
    ({"secid":"Si"},"EXACT_CONTRACT_REQUIRED"),
    ({"secid":"BR"},"EXACT_CONTRACT_REQUIRED"),
    ({"secid":"GOLD"},"EXACT_CONTRACT_REQUIRED"),
    ({"secid":" SBER"},"INVALID_EXACT_SECID"),
    ({"secid":"RTSI"},"STORED_INSTRUMENT_UNAVAILABLE"),
    ({"timeframe":"M1"},"UNSUPPORTED_TIMEFRAME"),
    ({"from_date":"2026-10-12T00:00:00"},"INVALID_DATE_RANGE"),
    ({"till_date":"2026-10-08"},"DATE_RANGE_EXCEEDS_BOUND"),
    ({"from_date":"2020-01-01"},"DATE_RANGE_EXCEEDS_BOUND"),
])
def test_invalid_or_unavailable_input_is_not_a_ready_sequence(kwargs,reason):
    svc=seeded()
    try:
        args=dict(secid="SBER",timeframe="D1",from_date="2026-10-09",till_date="2026-10-12");args.update(kwargs)
        with pytest.raises(StoredSessionSeriesError,match=reason):stored_session_series(svc,**args)
    finally:svc.store.close()


def test_empty_date_window_is_reported_not_filled():
    svc=seeded()
    try:
        got=stored_session_series(svc,secid="SBER",timeframe="D1",from_date="2026-10-10",till_date="2026-10-11")
        assert got["status"]=="STORED_SERIES_EMPTY" and got["candles"]==[]
        assert got["synthetic_bars_added"]==0 and got["session_calendar_status"]=="UNVERIFIED"
    finally:svc.store.close()


def test_exact_contract_never_reads_another_contract():
    svc=seeded()
    try:
        c=candles("D1")[0]
        for secid in ("SiZ6","SiH7"):svc.store.upsert_series(CandleSeries(instrument(secid),"D1",(c,)))
        got=stored_session_series(svc,secid="SiZ6",timeframe="D1",from_date="2026-10-09",till_date="2026-10-12")
        assert got["instrument"]["secid"]=="SiZ6" and got["count"]==1
    finally:svc.store.close()


@pytest.mark.parametrize("bad_end",["2026-10-08 00:00:00","2026-10-12 23:59:59"])
def test_invalid_or_overlapping_intervals_fail(bad_end):
    svc=seeded()
    try:
        svc.store._connection.execute("UPDATE historical_candles SET end_time=? WHERE begin LIKE '2026-10-09%'",[bad_end])
        with pytest.raises(StoredSessionSeriesError,match="OVERLAPPING"):
            stored_session_series(svc,secid="SBER",timeframe="D1",from_date="2026-10-09",till_date="2026-10-12")
    finally:svc.store.close()


def test_row_bound_fails_closed(monkeypatch):
    import birzha.application.stored_session_series as module
    svc=seeded()
    try:
        monkeypatch.setattr(module,"MAX_ROWS",1)
        with pytest.raises(StoredSessionSeriesError,match="ROW_BOUND"):
            stored_session_series(svc,secid="SBER",timeframe="D1",from_date="2026-10-09",till_date="2026-10-12")
    finally:svc.store.close()


def test_real_mcp_stdio_lists_and_reads_all_three_timeframes_without_network(tmp_path):
    runner=tmp_path/"isolated_mcp.py"
    runner.write_text("""import socket
from birzha.mcp import server
from birzha.domain.market import Instrument,Candle,CandleSeries
inst=Instrument(symbol='SBER',secid='SBER',board='TQBR',engine='stock',market='shares',asset_class='equity')
for tf,end in [('D1','23:49:59'),('H1','10:59:59'),('M15','10:14:59')]:
    for day,price in [('2026-10-12',130.75),('2026-10-09',100.25)]:
        begin='00:00:00' if tf=='D1' else '10:00:00'
        server._historical_store.upsert_series(CandleSeries(inst,tf,(Candle(price,price+1,price+2,price-1,None,None,day+' '+begin,day+' '+end),)))
original_connect=socket.socket.connect
def no_network(*a,**kw):raise AssertionError('External network is forbidden in isolated MCP acceptance')
def local_connect(self,address):
    # Windows asyncio creates its self-pipe with a loopback socket pair.
    if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):
        return original_connect(self,address)
    return no_network()
socket.socket.connect=local_connect
socket.create_connection=no_network
import httpx
httpx.Client.request=no_network
httpx.AsyncClient.request=no_network
server.mcp.run(transport='stdio')
""",encoding="utf-8")
    env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1]/"src"),
         "BIRZHA_STATE_BACKEND":"duckdb","BIRZHA_FORECAST_JOURNAL_PATH":":memory:",
         "BIRZHA_HISTORICAL_STORE_PATH":":memory:","BIRZHA_REQUIRE_MCP_AUTH":"false",
         "BIRZHA_MARKET_MIRROR_REQUIRED":"false","PYTHONIOENCODING":"utf-8"}
    env.pop("BIRZHA_MCP_BEARER_TOKEN",None)
    params=StdioServerParameters(command=sys.executable,args=[str(runner)],env=env)
    async def check():
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                tools=await client.list_tools()
                tool=next(t for t in tools.tools if t.name=="history.stored_series")
                assert tool.annotations.read_only_hint is True
                assert tool.annotations.open_world_hint is False
                for tf in ("D1","H1","M15"):
                    response=await client.call_tool("history.stored_series",arguments=dict(secid="SBER",timeframe=tf,from_date="2026-10-09",till_date="2026-10-12"))
                    assert not response.is_error
                    got=response.structured_content
                    if got is None:got=json.loads(response.content[0].text)
                    assert got["count"]==2 and got["price_adjustment"]=="NONE"
                    assert [c["open"] for c in got["candles"]]==[100.25,130.75]
                response=await client.call_tool("history.stored_series",arguments=dict(secid="BR",timeframe="D1",from_date="2026-10-09",till_date="2026-10-12"))
                got=response.structured_content
                if got is None:got=json.loads(response.content[0].text)
                assert got=={"status":"ERROR","reason":"EXACT_CONTRACT_REQUIRED"}
    asyncio.run(asyncio.wait_for(check(),timeout=45))
