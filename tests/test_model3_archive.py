from copy import deepcopy
from datetime import datetime,timedelta
import json
from pathlib import Path
import pytest
from birzha.application.model3_archive import Model3ArchiveUpdater
from birzha.application.model3_features import COLUMNS,BASE_COLUMNS,VERSION,research_features
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.application.market_mirror import D1_COLUMNS
from birzha.application.home_market_mirror import EXTRA_COLUMNS

RAW_COLUMNS=D1_COLUMNS+EXTRA_COLUMNS+["source_capture_label","source_file_id"]
def candles(tf="H1",n=35,sec="RIH6"):
    out=[RAW_COLUMNS]
    for i in range(n):
        start=datetime(2026,1,5,10)+timedelta(days=i) if tf=="D1" else datetime(2026,1,5,10)+timedelta(minutes=(60 if tf=="H1" else 15)*i)
        end=start+timedelta(seconds=(86400 if tf=="D1" else 3600 if tf=="H1" else 900)-1)
        t=start.isoformat()+"+03:00"
        d=dict(record_key=f"RI|{tf}|{sec}|{t}",calendar_date=t[:10],bar_start_time=t,bar_end_time=end.isoformat()+"+03:00",contract_code=sec,open=100+i,high=105+i,low=98+i,close=102+i,volume=100+i,is_complete=True,available_at_confidence="UNKNOWN",research_status="RECONSTRUCTED_RESEARCH_ONLY",normalized_sha256=f"hash{i}",source_archive_sha256="archive",source_id="OFFICIAL_SAVED",caveat="PIT_UNKNOWN")
        out.append([d.get(k,"") for k in RAW_COLUMNS])
    return out

class Book:
    def __init__(self,n=35,base=False):
        self.data={tf:candles(tf,n) for tf in ("D1","H1","M15")}
        self.data.update({tf+"_INDICATORS":[BASE_COLUMNS[:] if base else COLUMNS[:]] for tf in ("D1","H1","M15")})
        self.status={};self.writes=[];self.lose_once=False;self.mutate_on_read=False
    def validate_model3_target(self,symbol,sheet):
        assert symbol=="RI" and sheet in self.data
    def validate_service_target(self,symbol,sheet):assert sheet=="SYNC_STATUS"
    def read_status(self,symbol):return [["key","value"],*map(list,self.status.items())]
    def update_status(self,symbol,updates):self.status.update(updates)
    def read_rows(self,symbol,sheet):return deepcopy(self.data[sheet])
    def append_rows(self,symbol,sheet,rows):
        self.data[sheet]+=deepcopy(rows);self.writes.append(("append",sheet,len(rows)))
        if self.lose_once:self.lose_once=False;raise RuntimeError("response lost after commit")
    def model3_write_cells(self,symbol,sheet,edits):
        self.writes.append(("patch",sheet,len(edits)))
        for r,c,v in edits:
            while len(self.data[sheet])<=r:self.data[sheet].append([])
            while len(self.data[sheet][r])<=c:self.data[sheet][r].append("")
            self.data[sheet][r][c]=v
        width=len(self.data[sheet][0])
        self.data[sheet]=[row+[""]*(width-len(row)) for row in self.data[sheet]]
    def model3_order_rows(self,symbol,sheet,permutation):
        self.writes.append(("order",sheet,len(permutation)))
        self.data[sheet]=[self.data[sheet][0],*[self.data[sheet][i+1] for i in permutation]]

def service(book,tmp_path):
    return Model3ArchiveUpdater(book,audit_dir=str(tmp_path),source_sha="a"*40)
def run(svc,**kw):
    return svc.update(from_date="2026-01-01",till_date="2026-03-01",**kw)

def test_full_update_existing_tabs_exact_readback_and_noop(tmp_path):
    book=Book();svc=service(book,tmp_path);raw=deepcopy({tf:book.data[tf] for tf in ("D1","H1","M15")})
    result=run(svc);assert result["written"] is False and not book.writes
    result=run(svc,dry_run=False,exclusive_workbook_owner=True)
    assert result["status"]=="MODEL3_ARCHIVE_READBACK_PASS"
    assert len(COLUMNS)==119
    assert all(book.data[tf]==raw[tf] for tf in raw)
    assert all(len(book.data[tf+"_INDICATORS"])==36 for tf in raw)
    before=deepcopy(book.data);writes=len(book.writes)
    result=run(svc,dry_run=False,exclusive_workbook_owner=True)
    assert result["calculated_rows"]==0 and book.data==before and len(book.writes)==writes
    assert Path(result["backup"]).exists()

def test_missing_old_bars_sort_all_months_preserving_exact_rows(tmp_path):
    book=Book()
    for tf in ("D1","H1","M15"):book.data[tf]=[book.data[tf][0],*reversed(book.data[tf][1:])]
    original=deepcopy(book.data)
    result=run(service(book,tmp_path),dry_run=False,exclusive_workbook_owner=True)
    for tf in ("D1","H1","M15"):
        assert sorted(original[tf][1:],key=lambda r:r[4])==book.data[tf][1:]
    assert all(result["existing_order_repair"].get(tf) for tf in ("D1","H1","M15"))
    assert any(x[0]=="order" for x in book.writes)

def test_lost_append_reply_resumes_without_duplicates(tmp_path):
    book=Book();book.lose_once=True;svc=service(book,tmp_path)
    with pytest.raises(RuntimeError,match="lost"):run(svc,dry_run=False,exclusive_workbook_owner=True)
    assert book.status["model3_status"]=="FAILED_RECONCILE_BEFORE_RETRY"
    result=run(svc,dry_run=False,exclusive_workbook_owner=True)
    assert result["status"]=="MODEL3_ARCHIVE_READBACK_PASS"
    for tf in ("D1","H1","M15"):
        rs=book.data[tf+"_INDICATORS"][1:]
        assert len(rs)==35 and len({r[0] for r in rs})==35

def test_54_column_existing_tabs_expanded_without_new_tabs(tmp_path):
    book=Book(base=True);names=set(book.data)
    run(service(book,tmp_path),dry_run=False,exclusive_workbook_owner=True)
    assert set(book.data)==names
    assert all(book.data[tf+"_INDICATORS"][0]==COLUMNS for tf in ("D1","H1","M15"))

@pytest.mark.parametrize("mutation",["duplicate","wrong_contract","formula","bad_ohlc","negative_volume","foreign_header"])
def test_preflight_blocks_every_write(tmp_path,mutation):
    b=Book()
    if mutation=="duplicate":b.data["M15"].append(b.data["M15"][1][:])
    if mutation=="wrong_contract":b.data["M15"][1][8]="RIH6-RIM6"
    if mutation=="formula":b.data["H1_INDICATORS"].append(["=1"])
    if mutation=="bad_ohlc":b.data["D1"][1][10]=1
    if mutation=="negative_volume":b.data["D1"][1][13]=-1
    if mutation=="foreign_header":b.data["D1_INDICATORS"][0]=["unapproved"]
    with pytest.raises((MirrorConflict,ValueError)):run(service(b,tmp_path),dry_run=False,exclusive_workbook_owner=True)
    assert b.writes==[] and b.status=={}

def test_backfill_updates_only_proven_dependents_with_explicit_repair(tmp_path):
    b=Book();svc=service(b,tmp_path);removed=b.data["H1"].pop(3)
    run(svc,dry_run=False,exclusive_workbook_owner=True)
    b.data["H1"].append(removed)
    with pytest.raises(MirrorConflict,match="dependent feature conflict"):
        run(svc,dry_run=False,exclusive_workbook_owner=True)
    result=run(svc,dry_run=False,exclusive_workbook_owner=True,repair_dependent=True)
    assert result["repaired_cells"]>0
    assert len(b.data["H1_INDICATORS"])==36
    old=deepcopy(b.data)
    run(svc,dry_run=False,exclusive_workbook_owner=True)
    assert old==b.data

def test_prefix_causality_and_contract_reset():
    raw=candles(n=40);prefix=[raw[0],*raw[1:26]]
    a=research_features({"H1":prefix},calculated_at="frozen")["H1"]
    b=research_features({"H1":raw},calculated_at="frozen")["H1"]
    assert a==b[:25]
    second=candles(n=20,sec="RIM6")
    joined=research_features({"H1":[raw[0],*raw[1:],*second[1:]]},calculated_at="frozen")["H1"]
    d=research_features({"H1":second},calculated_at="frozen")["H1"]
    assert [r for r in joined if r[2]=="RIM6"]==d
    assert a[13][34]=="" and isinstance(a[14][34],float)
    assert all(r[COLUMNS.index("p3_signal_eligible")] is False for r in joined)

def test_zero_range_unknown_volume_and_late_pivot_not_promoted():
    raw=candles(n=6)
    for r in raw[1:]:r[9:13]=[100,100,100,100];r[13]=""
    rows=research_features({"H1":raw},calculated_at="t")["H1"]
    assert all(r[COLUMNS.index("p3_close_location")]=="" for r in rows)
    assert all(r[COLUMNS.index("p3_effort_per_progress")]=="" for r in rows)
    assert all(r[COLUMNS.index("p3_rvol_same_clock20_approx")]=="" for r in rows)
    raw=candles(n=6);raw[3][10]=200
    rows=research_features({"H1":raw},calculated_at="t")["H1"]
    j=COLUMNS.index("p3_confirmed_high_event")
    assert rows[2][j]=="" and rows[3][j]=="" and rows[4][j]==200

def test_requires_local_backup_and_exclusive_ownership(tmp_path):
    b=Book()
    with pytest.raises(MirrorConflict,match="sole writer"):run(service(b,tmp_path),dry_run=False)
    with pytest.raises(MirrorConflict,match="audit/backup"):run(Model3ArchiveUpdater(b),dry_run=False,exclusive_workbook_owner=True)
    assert b.writes==[]


def test_minute_capture_is_idempotent_hash_verified_and_exact_ri_only(tmp_path):
    from birzha.storage.model3_minute_archive import MinuteArchiveCapture
    from birzha.application.model3_minutes import load_minutes,minute_groups,profiles
    from birzha.domain.market import Instrument,Candle
    cap=MinuteArchiveCapture(tmp_path)
    ins=Instrument(symbol="RIH6",secid="RIH6",board="RFUD",engine="futures",market="forts",asset_class="future")
    minutes=tuple(Candle(open=100,high=120,low=100,close=100+i,volume=2,value=None,begin=f"2026-01-05 10:{i:02d}:00",end=f"2026-01-05 10:{i:02d}:59") for i in range(15))
    cap(ins,minutes,"2026-01-05","2026-01-05");cap(ins,minutes,"2026-01-05","2026-01-05")
    assert len(list(tmp_path.glob("*.zip")))==1
    rows,status=load_minutes(tmp_path,"2026-01-05","2026-01-05")
    assert len(rows)==15 and status.startswith("HASH_VERIFIED")
    groups=minute_groups(rows,"2026-01-05","2026-01-05");p=profiles(groups,set(groups))
    assert p[("RIH6","M15","2026-01-05 10:00:00")]["volume"]==30
    wrong=Instrument(symbol="SBER",secid="SBER",board="TQBR",engine="stock",market="shares",asset_class="equity")
    cap(wrong,minutes,"2026-01-05","2026-01-05")
    assert len(list(tmp_path.glob("*.zip")))==1

def test_missing_m15_restored_by_existing_provider_aggregator(tmp_path):
    from birzha.storage.model3_minute_archive import MinuteArchiveCapture
    from birzha.domain.market import Instrument,Candle
    b=Book(n=1);b.data["M15"]=[b.data["M15"][0]]
    ins=Instrument(symbol="RIH6",secid="RIH6",board="RFUD",engine="futures",market="forts",asset_class="future")
    source=(Candle(open=100,high=105,low=98,close=102,volume=100,value=None,begin="2026-01-05 10:00:00",end="2026-01-05 10:00:59"),)
    folder=tmp_path/"minutes";MinuteArchiveCapture(folder)(ins,source,"2026-01-05","2026-01-05")
    svc=Model3ArchiveUpdater(b,minute_archive_dir=str(folder),audit_dir=str(tmp_path/"audit"))
    result=run(svc,dry_run=False,exclusive_workbook_owner=True)
    assert result["planned_additions"]["M15"]==1
    raw=dict(zip(b.data["M15"][0],b.data["M15"][1]))
    assert raw["volume"]==100 and raw["source_candles"]==1
    assert raw["is_complete"] is True and "SPARSE" in raw["source_kind"]
    features=dict(zip(COLUMNS,b.data["M15_INDICATORS"][1]))
    assert features["source_complete_flag"] is False
    assert features["p3_signal_eligible"] is False
    assert features["p3_minute_sign_delta_proxy"]==0

def test_native_transport_109_columns_and_whole_row_order_requests():
    import httpx
    from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
    from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS,INSTRUMENT_FOLDER_IDS
    target=INSTRUMENT_TARGETS["RI"];seen=[];sheet_id=10
    data=[COLUMNS]+[["key"+str(i),"RI","RIH6","H1",t] for i,t in enumerate(["2026-01-07 10:00:00","2026-01-08 10:00:00","2026-01-06 10:00:00"])]
    def handler(req):
        if "/drive/" in str(req.url):return httpx.Response(200,json={"id":target.spreadsheet_id,"name":target.spreadsheet_name,"mimeType":"application/vnd.google-apps.spreadsheet","trashed":False,"parents":[INSTRUMENT_FOLDER_IDS["RI"]]})
        if req.method=="POST":
            seen.extend(json.loads(req.content)["requests"]);return httpx.Response(200,json={})
        if "/values/" in str(req.url):
            assert "DE" in str(req.url)
            return httpx.Response(200,json={"values":data})
        return httpx.Response(200,json={"spreadsheetId":target.spreadsheet_id,"sheets":[{"properties":{"sheetId":sheet_id,"title":"H1_INDICATORS","sheetType":"GRID","gridProperties":{"rowCount":50,"columnCount":109}}}]})
    t=NativeCanonicalSheets(lambda:"isolated-test",transport=httpx.MockTransport(handler))
    t.validate_model3_target("RI","H1_INDICATORS")
    assert t.read_rows("RI","H1_INDICATORS")==data
    t.model3_order_rows("RI","H1_INDICATORS",[2,0,1])
    move=next(x["moveDimension"] for x in seen if "moveDimension" in x)
    assert move["source"]["startIndex"]==3 and move["destinationIndex"]==1
    assert not any("updateCells" in x for x in seen)

def test_source_export_filters_ri_exact_futures_and_excludes_cash_index():
    from birzha.storage.historical_store import DuckDBHistoricalCandleStore
    from birzha.domain.market import Instrument,Candle,CandleSeries
    db=DuckDBHistoricalCandleStore(":memory:")
    try:
        for sec,engine,market,asset in [("RIH6","futures","forts","future"),("RTSI","stock","index","index"),("RIH6-RIM6","futures","forts","future")]:
            ins=Instrument(symbol="RI",root_symbol="RI",secid=sec,board="RFUD",engine=engine,market=market,asset_class=asset)
            db.upsert_series(CandleSeries(ins,"H1",(Candle(open=100,high=105,low=98,close=102,volume=100,value=None,begin="2026-01-05 10:00:00",end="2026-01-05 10:59:59"),)))
        bundle=db.export_mirror_bundle("RI","2026-01-05","2026-01-05")
        assert [r["secid"] for r in bundle["candles"]]==["RIH6"]
    finally:db.close()

def test_real_stdio_mcp_model3_cycle_and_existing_mirror_route(tmp_path):
    import asyncio,os,sys
    from pathlib import Path
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters,stdio_client
    root=Path(__file__).resolve().parents[1]
    runner=tmp_path/"isolated_model3_mcp.py"
    runner.write_text(
        "import sys,socket,dataclasses\n"
        f"sys.path.insert(0,{str(root/'tests')!r})\n"
        "from test_model3_archive import Book\n"
        "from birzha.mcp import server\n"
        "from birzha.application.model3_archive import Model3ArchiveUpdater\n"
        "def forbidden(*a,**kw):raise AssertionError('No external network in isolated acceptance')\n"
        "original_connect=socket.socket.connect\n"
        "def local(self,address):\n"
        " if isinstance(address,tuple) and address[0] in ('127.0.0.1','::1'):return original_connect(self,address)\n"
        " return forbidden()\n"
        "socket.socket.connect=local\n"
        "import httpx\n"
        "httpx.Client.request=forbidden\n"
        "httpx.AsyncClient.request=forbidden\n"
        "server.settings=dataclasses.replace(server.settings,native_mirror_sole_writer=True)\n"
        f"server._model3_archive=Model3ArchiveUpdater(Book(n=5),audit_dir={str(tmp_path/'audit')!r})\n"
        "server.mcp.run(transport='stdio')\n",encoding="utf-8")
    env={**os.environ,"PYTHONPATH":str(root/"src"),"BIRZHA_STATE_BACKEND":"duckdb","BIRZHA_FORECAST_JOURNAL_PATH":":memory:","BIRZHA_HISTORICAL_STORE_PATH":":memory:","BIRZHA_NATIVE_MIRROR_ENABLED":"false","BIRZHA_REQUIRE_MCP_AUTH":"false","PYTHONIOENCODING":"utf-8"}
    params=StdioServerParameters(command=sys.executable,args=[str(runner)],env=env)
    async def check():
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                tools=await client.list_tools()
                assert {"archive.update","archive.sync","archive.collect","archive.contract","archive.contract_plan"} <= {t.name for t in tools.tools}
                reply=await client.call_tool("archive.contract",arguments={"symbol":"RI"})
                contract=reply.structured_content or json.loads(reply.content[0].text)
                assert contract["canonical_storage"]=="GOOGLE_SHEETS"
                assert len(contract["indicator_sets"][0]["outputs"])==55
                args=dict(from_date="2026-01-01",till_date="2026-03-01")
                for dry,status in [(True,"MODEL3_ARCHIVE_DRY_RUN_READY"),(False,"MODEL3_ARCHIVE_READBACK_PASS"),(False,"MODEL3_ARCHIVE_READBACK_PASS")]:
                    reply=await client.call_tool("archive.update",arguments={**args,"dry_run":dry})
                    result=reply.structured_content or json.loads(reply.content[0].text)
                    assert result["status"]==status and result["forecast_admission"]=="NOT_GRANTED"
                reply=await client.call_tool("mirror.sync",arguments={**args,"symbol":"RI"})
                result=reply.structured_content or json.loads(reply.content[0].text)
                assert result["status"]=="MODEL3_ARCHIVE_DRY_RUN_READY" and result["calculated_rows"]==0
    asyncio.run(asyncio.wait_for(check(),timeout=45))

def test_new_saved_minutes_fill_previously_unavailable_fields(tmp_path):
    from birzha.storage.model3_minute_archive import MinuteArchiveCapture
    from birzha.domain.market import Instrument,Candle
    b=Book(n=1);folder=tmp_path/"minutes";folder.mkdir()
    s=Model3ArchiveUpdater(b,minute_archive_dir=str(folder),audit_dir=str(tmp_path/"audit"))
    run(s,dry_run=False,exclusive_workbook_owner=True)
    assert b.data["H1_INDICATORS"][1][98]=="UNAVAILABLE"
    ins=Instrument(symbol="RI",root_symbol="RI",secid="RIH6",engine="futures",market="forts",board="RFUD",asset_class="future")
    MinuteArchiveCapture(folder)(ins,(Candle(open=100,high=105,low=98,close=102,volume=100,value=None,begin="2026-01-05 10:00:00",end="2026-01-05 10:00:59"),),"2026-01-05","2026-01-05")
    result=run(s,dry_run=False,exclusive_workbook_owner=True)
    assert result["calculated_rows"]==2
    assert b.data["H1_INDICATORS"][1][98]!="UNAVAILABLE"
    assert b.data["H1_INDICATORS"][1][97]==0
    assert run(s,dry_run=False,exclusive_workbook_owner=True)["calculated_rows"]==0
