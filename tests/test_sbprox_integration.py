import hashlib
import io
import json
import zipfile
from datetime import date, datetime
from types import SimpleNamespace

import duckdb
import pytest
from test_sbprox_archive import reference, row
from birzha.application.sbprox_archive import build
from birzha.application.sbprox_integration import aggregate, sheet_plan, source_contract, OUTPUTS
from birzha.providers.sbprox_ri import MOSCOW
from birzha.storage.google_archive_transport import GoogleArchiveTransport

NOW = datetime(2026, 11, 9, tzinfo=MOSCOW)

@pytest.fixture
def source(tmp_path, reference):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "2026-02-11.locchache").write_bytes(
        b"".join(row(hhmm=f"09{i:02}") for i in range(15)))
    root, _ = build(cache, tmp_path / "out", reference, date(2026,2,11),date(2026,2,11))
    content = io.BytesIO()
    tables, hashes = {}, {}
    with zipfile.ZipFile(content, "w") as z:
        for table, count in (("M1_BARS",15),("PRICE_LEVELS",15)):
            p = next((root / "NORMALIZED").glob(table+"*.parquet"))
            body = p.read_bytes()
            name = "TABLES/"+table+".parquet"
            z.writestr(name, body)
            hashes[name] = hashlib.sha256(body).hexdigest()
            tables[table] = {"rows":count}
        z.writestr("MANIFEST.json",json.dumps(dict(schema="RI_SBPROX_2026_MASTER_V1",
            generation="test",checksums=hashes,tables=tables)))
    body=content.getvalue()
    return dict(body=body,sha256=hashlib.sha256(body).hexdigest(),version="1")

def calculate(source, end="2026-02-11T09:15:00+03:00",tf="M15"):
    return aggregate(source,"RIH6",tf,"2026-02-11T09:00:00+03:00",end,NOW)

def test_aggregation_and_partial_minute(source):
    full=calculate(source)["rows"][0]
    assert full["minutes"]==15
    assert full["values"]["sbprox_delta_contracts"]==60
    assert full["values"]["sbprox_volume_contracts"]==150
    partial=calculate(source,"2026-02-11T09:14:30+03:00")["rows"][0]
    assert partial["minutes"]==14
    assert partial["values"]["sbprox_quality"]=="PARTIAL_OR_SESSION_UNVERIFIED"
    assert calculate(source,tf="D1")["rows"][0]["values"]["sbprox_quality"]=="PARTIAL_OR_SESSION_UNVERIFIED"
    with pytest.raises(ValueError,match="FUTURE"):
        aggregate(source,"RIH6","M15","2026-02-11T09:00:00+03:00","2026-02-11T09:15:00+03:00",
                  datetime(2026,2,11,9,5,tzinfo=MOSCOW))
    with pytest.raises(ValueError,match="EXACT"):
        aggregate(source,"RTSI","M15","2026-02-11T09:00:00+03:00","2026-02-11T09:15:00+03:00",NOW)
    with pytest.raises(ValueError,match="HASH"):
        calculate(dict(source,sha256="0"*64))

def test_plan_requires_columns_preserves_filled_and_rejects_price_mismatch(source):
    result=calculate(source)
    ch=["contract_code","bar_start_time","open","high","low","close","volume","is_complete"]
    cr=[ch,["RIH6","2026-02-11T09:00:00+03:00",100,100,100,100,150,True]]
    ir=[["secid","begin"],["RIH6",cr[1][1]]]
    plan=sheet_plan(result,cr,ir)
    assert plan["proposed_columns"]==list(OUTPUTS)
    assert plan["rows"][0]["cells"]==[]
    ir[0].extend(OUTPUTS)
    ir[1].extend([999]+[""]*(len(OUTPUTS)-1))
    plan=sheet_plan(result,cr,ir)
    assert all(c["field"]!="sbprox_volume_contracts" for c in plan["rows"][0]["cells"])
    assert ir[1][2]==999
    cr[1][-1]=False
    assert "CANONICAL_SOURCE_NOT_COMPLETE" in sheet_plan(result,cr,ir)["rows"][0]["reasons"]
    cr[1][-1]=True
    cr[1][-2]=149
    assert "CANONICAL_OHLCV_MISMATCH" in sheet_plan(result,cr,ir)["rows"][0]["reasons"]

def test_drive_cache_and_revision_change(source):
    contract=source_contract()
    transport=GoogleArchiveTransport(lambda:"test")
    meta=dict(id=contract["file_id"],name=contract["file_name"],parents=[contract["folder_id"]],
              mimeType="application/zip",trashed=False,version="1",
              size=str(len(source["body"])),sha256Checksum=source["sha256"])
    reads=[]
    transport._request=lambda *a,**k:meta.copy()
    transport._media=lambda *a,**k:reads.append(1) or source["body"]
    assert transport.read_sbprox_master()["sha256"]==source["sha256"]
    transport.read_sbprox_master()
    assert len(reads)==1
    transport._sbprox_master_cache=None
    answers=iter([meta,dict(meta,version="2")])
    transport._request=lambda *a,**k:next(answers)
    with pytest.raises(Exception,match="CHANGED"):
        transport.read_sbprox_master()

def test_actual_mcp_source_commands(source):
    import asyncio
    import httpx
    from birzha.mcp.archive_server import build_app

    transport=SimpleNamespace(read_sbprox_master=lambda:source)
    service=SimpleNamespace(transport=transport,now=lambda:NOW)
    app=build_app(service,"test-token")
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://127.0.0.1") as client:
                headers={"Authorization":"Bearer test-token","Accept":"application/json, text/event-stream"}
                r=await client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1"}}})
                if r.headers.get("mcp-session-id"):headers["mcp-session-id"]=r.headers["mcp-session-id"]
                headers["MCP-Protocol-Version"]=r.json()["result"]["protocolVersion"]
                await client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","method":"notifications/initialized"})
                r=await client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":2,"method":"tools/list"})
                names={t["name"] for t in r.json()["result"]["tools"]}
                assert {"archive.sbprox_contract","archive.sbprox_features","archive.sbprox_sheet_plan"}<=names
                r=await client.post("/mcp",headers=headers,json={"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"archive.sbprox_features","arguments":{"exact_secid":"RIH6","timeframe":"M15","start":"2026-02-11T09:00:00+03:00","end":"2026-02-11T09:15:00+03:00"}}})
                payload=r.json()["result"]
                result=payload.get("structuredContent") or json.loads(payload["content"][0]["text"])
                assert result["written"] is False
                assert result["rows"][0]["values"]["sbprox_delta_contracts"]==60
    asyncio.run(run())


@pytest.mark.parametrize("difference", [None, "missing", "volume", "clipped"])
def test_sparse_hour_requires_exact_indexed_minute_match(source, difference):
    from datetime import timedelta
    from birzha.application.sbprox_integration import MATCHED_SPARSE_WINDOW
    start = datetime(2026, 2, 11, 9, tzinfo=MOSCOW)
    minutes = {start + timedelta(minutes=i): (100,100,100,100,10) for i in range(15)}
    if difference == "missing":
        minutes[start + timedelta(minutes=59)] = (100,100,100,100,10)
    if difference == "volume":
        minutes[start] = (100,100,100,100,11)
    evidence = {"file_id":"indexed-moex", "sha256":"1"*64, "secid":"RIH6", "month":"2026-02"}
    end = start + timedelta(minutes=59 if difference == "clipped" else 60)
    result = aggregate(source,"RIH6","H1",start.isoformat(),end.isoformat(),NOW,
                       reference_minutes=lambda *args:(minutes,evidence,None))
    got = result["rows"][0]
    assert (got["values"]["sbprox_quality"] == MATCHED_SPARSE_WINDOW) is (difference is None)
    assert got["moex_m1_reference"] == (evidence if difference is None else None)
    if difference is None:
        header = ["contract_code","bar_start_time","open","high","low","close","volume","is_complete"]
        candles = [header,["RIH6",start.isoformat(),100,100,100,100,150,True]]
        indicators = [["secid","begin",*OUTPUTS],["RIH6",start.isoformat()]]
        assert len(sheet_plan(result,candles,indicators)["rows"][0]["cells"]) == 12
        candles[1][-1] = False
        assert not sheet_plan(result,candles,indicators)["rows"][0]["cells"]


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("changed", [None, "end", "hash", "file", "volume", "last_minute", "complete_flag", "standard_end"])
def test_nonstandard_hour_needs_original_indexed_h1_proof(source, changed, complete):
    from datetime import timedelta
    from birzha.application.sbprox_integration import MATCHED_NONSTANDARD_WINDOW
    start = datetime(2026, 2, 11, 9, tzinfo=MOSCOW)
    begin = start.isoformat()
    finish = (start + timedelta(minutes=59,seconds=20)).isoformat()
    if changed == "standard_end":
        finish = (start + timedelta(minutes=59, seconds=59)).isoformat()
    minutes = {start+timedelta(minutes=i): (100,100,100,100,10) for i in range(15)}
    evidence = dict(secid="RIH6", month="2026-02",file_id="indexed-moex",sha256="1"*64)
    proof = dict(secid="RIH6",begin=begin,end=finish,is_complete=complete,normalized_sha256="source-hash",
                 open=100,high=100,low=100,close=100,volume=150)
    got = aggregate(source,"RIH6","H1",begin,(start+timedelta(hours=1)).isoformat(),NOW,
                    reference_minutes=lambda *args:(minutes,evidence,proof))
    header = ["record_key","contract_code","bar_start_time","bar_end_time","open","high","low",
              "close","volume","is_complete","normalized_sha256","source_file_id"]
    raw = ["raw-key","RIH6",begin,finish,100,100,100,100,150,complete,"source-hash","indexed-moex"]
    ih = ["symbol","secid","timeframe","begin","end","source_record_key","source_record_sha256",
          "source_complete_flag",*OUTPUTS]
    target = ["RI","RIH6","H1",begin,finish,"raw-key","source-hash",complete]
    if changed == "end": target[4] = begin
    if changed == "hash": raw[10] = "other"
    if changed == "file": raw[11] = "other"
    if changed == "volume": proof["volume"] = 151
    if changed == "last_minute": got["rows"][0]["last_minute"] = (start+timedelta(hours=1)).isoformat()
    if changed == "complete_flag": proof["is_complete"] = not complete
    planned = sheet_plan(got,[header,raw],[ih,target])["rows"][0]
    if changed in (None, "standard_end"):
        assert len(planned["cells"])==12
        expected_quality = MATCHED_NONSTANDARD_WINDOW
        if changed == "standard_end" and complete:
            expected_quality = "MATCHED_INDEXED_MOEX_M1_RESEARCH"
        assert {c["field"]:c["value"] for c in planned["cells"]}["sbprox_quality"]==expected_quality
        assert raw[9] is complete and raw[3] == finish
    else:
        assert planned["cells"]==[]
        if changed == "file":
            rechecked = sheet_plan(got,[header,raw],[ih,target],
                archive_matches=lambda original, checksum: (original, checksum) == ("other", "1"*64))["rows"][0]
            assert len(rechecked["cells"]) == 12
            assert rechecked["moex_m1_reference"]["original_file_id"] == "other"


def test_aggregation_uses_exact_canonical_interval(source):
    from birzha.application.sbprox_integration import aggregate_open_master, open_master
    start = datetime.fromisoformat('2026-02-11T09:03:00+03:00')
    end = datetime.fromisoformat('2026-02-11T09:10:30+03:00')
    with open_master(source) as master:
        got = aggregate_open_master(source, master, 'RIH6','H1',
            '2026-02-11T09:00:00+03:00','2026-02-11T10:00:00+03:00',NOW,
            canonical_windows={start:end})['rows']
    assert len(got) == 1
    assert got[0]['begin'] == start.isoformat()
    assert got[0]['last_minute'] == '2026-02-11T09:10:00+03:00'
    assert got[0]['minutes'] == 8 and got[0]['ohlcv'][-1] == 80
    assert got[0]['values']['sbprox_volume_contracts'] == 80
