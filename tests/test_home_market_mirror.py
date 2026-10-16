from copy import deepcopy
from pathlib import Path
import json
import httpx
import pytest
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.application.home_market_mirror import HomeCanonicalMirror, project_source, append_service
from birzha.storage.historical_store import DuckDBHistoricalCandleStore
from birzha.domain.market import Instrument, Candle, CandleSeries
from birzha.storage.google_mirror_auth import GoogleRefreshToken
from birzha.config import Settings


def store(symbol="SBER"):
    db=DuckDBHistoricalCandleStore()
    instrument=Instrument(symbol=symbol,secid=symbol,board="TQBR",engine="stock",market="shares",asset_class="equity")
    for tf,end in [("D1","2026-10-12 23:59:59"),("H1","2026-10-12 10:59:59"),("M15","2026-10-12 10:14:59")]:
        candle=Candle(open=100,close=101,high=102,low=99,volume=10,value=1000,begin="2026-10-12 10:00:00",end=end,available_at=end,available_at_confidence="INFERRED")
        db.upsert_series(CandleSeries(instrument,tf,(candle,)))
        db.mark_verified(symbol,tf,"2026-10-12","2026-10-12")
    db.record_sessions(symbol,symbol,("2026-10-12",))
    return db

class Book:
    def __init__(self):
        self.data={"D1":[],"H1":[],"M15":[],"SESSIONS":[],"VERIFIED_RANGES":[],"SYNC_STATUS":[["key","value"],["primary_store","YDB"],["archive_receipt","keep"]]}
        self.writes=[]; self.fail_sheet=None
    def validate_target(self,symbol,sheet): pass
    validate_service_target=validate_target
    def read_rows(self,symbol,sheet): return deepcopy(self.data[sheet])
    read_service_rows=read_rows
    def append_rows(self,symbol,sheet,rows):
        self.writes.append(sheet)
        if sheet == self.fail_sheet: raise MirrorConflict("injected failure")
        self.data[sheet]+=deepcopy(rows)
    append_service_rows=append_rows
    def read_status(self,symbol): return self.read_rows(symbol,"SYNC_STATUS")
    def update_status(self,symbol,updates):
        self.writes.append("SYNC_STATUS")
        rows=self.data["SYNC_STATUS"];pos={r[0]:i for i,r in enumerate(rows[1:],1)}
        for key,value in updates.items():
            if key in pos:rows[pos[key]][1]=value
            else:rows.append([key,value])


def test_full_stored_source_three_frames_service_and_status_without_price_writes_to_db():
    db=store(); before=db._connection.execute("SELECT * FROM historical_candles ORDER BY timeframe").fetchall()
    book=Book(); svc=HomeCanonicalMirror(db,book,"a"*40)
    dry=svc.sync(symbol="SBER",from_date="2026-10-12",till_date="2026-10-12")
    assert dry["status"]=="MIRROR_DRY_RUN_READY" and book.writes == []
    result=svc.sync(symbol="SBER",from_date="2026-10-12",till_date="2026-10-12",dry_run=False,exclusive_workbook_owner=True)
    assert result["status"]=="MIRROR_SYNC_PASS"
    assert [row for row in book.data["SYNC_STATUS"] if row[0]=="archive_receipt"] == [["archive_receipt","keep"]]
    for tf in ("D1","H1","M15"):
        assert len(book.data[tf])==2
        assert book.data[tf][1][6:8] == ["","UNKNOWN"]
        assert book.data[tf][1][2:4] == ["",""]
    assert len(book.data["SESSIONS"])==2 and len(book.data["VERIFIED_RANGES"])==4
    assert db._connection.execute("SELECT * FROM historical_candles ORDER BY timeframe").fetchall()==before
    svc.sync(symbol="SBER",from_date="2026-10-12",till_date="2026-10-12",dry_run=False,exclusive_workbook_owner=True)
    assert all(len(book.data[tf])==2 for tf in ("D1","H1","M15","SESSIONS"))
    db.close()


def test_semantic_key_alias_preserves_original_spelling_provenance():
    db=store(); bundle=db.export_mirror_bundle("SBER","2026-10-12","2026-10-12")
    projected,_=project_source(bundle,"H1",[])
    original=deepcopy(projected); original[1][0]="SBER|H1|SBER|2026-10-12 10:00:00";original[1][4]="2026-10-12 10:00:00"; original[1][19]="OLDER_RESEARCH_SOURCE"
    incoming,evidence=project_source(bundle,"H1",original)
    assert incoming == original and evidence["preserved_different_provenance"]==1
    db.close()


def test_conflict_in_last_frame_blocks_all_writes_including_status():
    db=store();bundle=db.export_mirror_bundle("SBER","2026-10-12","2026-10-12");book=Book()
    book.data["M15"],_=project_source(bundle,"M15",[]);book.data["M15"][1][12]=100.5
    before=deepcopy(book.data)
    with pytest.raises(MirrorConflict):HomeCanonicalMirror(db,book,"a"*40).sync(symbol="SBER",from_date="2026-10-12",till_date="2026-10-12",dry_run=False,exclusive_workbook_owner=True)
    assert not book.writes and book.data==before
    db.close()


def test_partial_write_failure_never_publishes_pass_and_resume_deduplicates():
    db=store();book=Book();book.fail_sheet="H1";svc=HomeCanonicalMirror(db,book,"a"*40)
    with pytest.raises(MirrorConflict):svc.sync(symbol="SBER",from_date="2026-10-12",till_date="2026-10-12",dry_run=False,exclusive_workbook_owner=True)
    assert dict(book.data["SYNC_STATUS"][1:])["status"] == "MIRROR_SYNC_FAILED"
    assert len(book.data["D1"])==2
    book.fail_sheet=None
    assert svc.sync(symbol="SBER",from_date="2026-10-12",till_date="2026-10-12",dry_run=False,exclusive_workbook_owner=True)["status"]=="MIRROR_SYNC_PASS"
    assert len(book.data["D1"])==2
    db.close()


def test_concurrent_mirror_refuses_without_writes():
    db=store();book=Book();svc=HomeCanonicalMirror(db,book,"a"*40);svc._lock.acquire()
    with pytest.raises(MirrorConflict):svc.sync(symbol="SBER",from_date="2026-10-12",till_date="2026-10-12")
    svc._lock.release();assert not book.writes;db.close()


def test_missing_source_frame_does_not_fake_success():
    db=store();db._connection.execute("DELETE FROM historical_candles WHERE timeframe='M15'");book=Book()
    with pytest.raises(MirrorConflict,match="no M15"):HomeCanonicalMirror(db,book,"a"*40).sync(symbol="SBER",from_date="2026-10-12",till_date="2026-10-12")
    assert not book.writes;db.close()


def test_sessions_symbol_case_and_repeated_source_evidence_not_duplicated():
    old=[["symbol","secid","trade_date"],["Si","SiZ6","2026-10-12"]]
    assert append_service(old,old[0],[["SI","SiZ6","2026-10-12"],["SI","SiZ6","2026-10-12"]])==[]


def test_export_scope_safe_and_no_network_other_markets_or_writes():
    db=store();assert db.export_mirror_bundle("IMOEX","2026-10-12","2026-10-12")["candles"]==[]
    with pytest.raises(ValueError):db.export_mirror_bundle("SBER","2025-01-01","2026-10-12")
    with pytest.raises(ValueError):db.export_mirror_bundle("SBER; DROP TABLE historical_candles","2026-10-12","2026-10-12")
    assert len(db.export_mirror_bundle("SBER","2026-10-12","2026-10-12")["candles"])==3;db.close()


def test_refresh_token_cache_and_safe_failure(tmp_path):
    path=tmp_path/"explicit.json";path.write_text(json.dumps({"client_id":"test","client_secret":"test","refresh_token":"test"}))
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(200,json={"access_token":"dummy","expires_in":3600,"token_type":"Bearer"})
    auth=GoogleRefreshToken(str(path),transport=httpx.MockTransport(handler))
    assert auth()==auth()=="dummy" and len(calls)==1
    auth._expires=0;auth.transport=httpx.MockTransport(lambda req:httpx.Response(400,json={"error":"do not print private body"}))
    with pytest.raises(MirrorConflict) as exc:auth()
    assert "private" not in str(exc.value)

@pytest.mark.parametrize("missing", ["BIRZHA_NATIVE_MIRROR_CREDENTIALS_PATH","BIRZHA_NATIVE_MIRROR_SOLE_WRITER","BIRZHA_REQUIRE_MCP_AUTH","BIRZHA_SOURCE_SHA"])
def test_native_config_requires_explicit_access_owner_auth_revision(monkeypatch, missing):
    for key,value in {"BIRZHA_NATIVE_MIRROR_ENABLED":"true","BIRZHA_STATE_BACKEND":"duckdb","BIRZHA_NATIVE_MIRROR_CREDENTIALS_PATH":"test-only.json","BIRZHA_NATIVE_MIRROR_SOLE_WRITER":"true","BIRZHA_REQUIRE_MCP_AUTH":"true","BIRZHA_MCP_BEARER_TOKEN":"dummy","BIRZHA_SOURCE_SHA":"a"*40}.items():monkeypatch.setenv(key,value)
    monkeypatch.delenv("BIRZHA_SOURCE_COMMIT",raising=False);monkeypatch.delenv(missing,raising=False)
    with pytest.raises(ValueError):Settings.from_env()

def test_native_service_status_updates_only_requested_cells_and_preserves_legacy():
    from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
    from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS, INSTRUMENT_FOLDER_IDS
    target=INSTRUMENT_TARGETS['SBER'];data=[["key","value"],["primary_store","YDB"],["receipt","keep"],["status","OLD"]];posts=[]
    def handler(request):
        if request.url.host=='www.googleapis.com':return httpx.Response(200,json={"id":target.spreadsheet_id,"name":target.spreadsheet_name,"mimeType":"application/vnd.google-apps.spreadsheet","parents":[INSTRUMENT_FOLDER_IDS['SBER']],"trashed":False})
        if request.method=='POST':
            body=json.loads(request.content);posts.append(body)
            for op in body['requests']:
                if 'updateCells' in op:
                    arg=op['updateCells']; assert arg['fields']=='userEnteredValue'
                    data[arg['start']['rowIndex']]=[next(iter(x['userEnteredValue'].values())) for x in arg['rows'][0]['values']]
                else:
                    assert list(op)==['appendCells']
                    data.extend([[next(iter(x['userEnteredValue'].values())) for x in row['values']] for row in op['appendCells']['rows']])
            return httpx.Response(200,json={'replies':[]})
        if '/values/' in request.url.path:return httpx.Response(200,json={'values':data})
        return httpx.Response(200,json={'spreadsheetId':target.spreadsheet_id,'sheets':[{'properties':{'sheetId':77,'title':'SYNC_STATUS','sheetType':'GRID','gridProperties':{'rowCount':100,'columnCount':2}}}]})
    transport=NativeCanonicalSheets(lambda:'dummy',transport=httpx.MockTransport(handler))
    transport.update_status('SBER',{'primary_store':'DuckDB','status':'MIRROR_SYNC_PASS'})
    assert dict(data[1:])['receipt']=='keep'
    assert dict(data[1:])['legacy_pre_mirror_v3_primary_store']=='YDB'
    assert dict(data[1:])['primary_store']=='DuckDB'
    assert len(posts)==1


def test_mcp_mirror_disabled_and_configured_paths(monkeypatch):
    import birzha.mcp.server as server
    from types import SimpleNamespace
    monkeypatch.setattr(server,'_native_mirror',None)
    assert server.mirror_sync('SBER','2026-10-12','2026-10-12')['status']=='BLOCKED'
    calls=[]
    class Service:
        def sync(self,**kwargs):calls.append(kwargs);return {'status':'MIRROR_DRY_RUN_READY','symbol':kwargs['symbol']}
    monkeypatch.setattr(server,'_native_mirror',Service())
    monkeypatch.setattr(server,'settings',SimpleNamespace(native_mirror_sole_writer=True))
    assert server.mirror_sync('SBER','2026-10-12','2026-10-12')['status']=='MIRROR_DRY_RUN_READY'
    assert server.mirror_sync_core('2026-10-12','2026-10-12')['passed']==6
    assert all(call['dry_run'] is True for call in calls)
@pytest.mark.parametrize('root,secid', [('Si','SiZ6'),('BR','BRV6'),('GOLD','GDZ6')])
def test_stored_future_root_exact_contract_and_versioned_session_keys(root,secid):
    db=DuckDBHistoricalCandleStore()
    instrument=Instrument(symbol=secid,secid=secid,root_symbol=root,board='RFUD',engine='futures',market='forts',asset_class='future')
    db.upsert_series(CandleSeries(instrument,'H1',(Candle(open=100,close=101,high=102,low=99,volume=10,value=1000,begin='2026-10-12 10:00:00',end='2026-10-12 10:59:59'),)))
    db.record_sessions(root+'#ROLLING_HISTORY_V2_PREWARM#D1_SESSION_V2_ACTIVITY',secid,('2026-10-12',))
    bundle=db.export_mirror_bundle(root,'2026-10-12','2026-10-12')
    assert bundle['sessions']==[[root.upper(),secid,'2026-10-12']]
    rows,_=project_source(bundle,'H1',[])
    assert rows[1][8]==secid and rows[1][0].startswith(root.upper()+'|H1|'+secid+'|')
    db.close()

def test_current_main_br_quarantine_is_not_exported_or_promoted():
    from birzha.application.br_research_repair import reconcile, digest
    db = DuckDBHistoricalCandleStore(":memory:")
    proposal = {"status": "PROPOSAL_ONLY_NOT_APPROVED_FOR_IMPORT",
        "source_book": "1y8nMiqmMKv1af_lRQjGPCYDKpz3P1nfojkPjqqa4JjQ", "count": 1,
        "records": [{"timeframe": "H1", "record_quality": "RESEARCH_NOT_ADMITTED",
            "forecast_admission": "NOT_GRANTED", "sheet_row": {
                "contract_code": "BRZ5", "bar_start_time": "2025-11-01T08:00:00+03:00",
                "bar_end_time": "2025-11-01T08:59:59+03:00", "open": 10., "high": 12.,
                "low": 9., "close": 11., "volume": 20., "is_complete": True}}]}
    try:
        reconcile(db, proposal, digest(proposal), apply=True)
        before = db._connection.execute("SELECT * FROM br_research_quarantine").fetchall()
        bundle = db.export_mirror_bundle("BR", "2025-11-01", "2025-11-01")
        assert bundle["candles"] == []
        assert bundle["calendar_attestation"] == "UNVERIFIED"
        assert db._connection.execute("SELECT * FROM br_research_quarantine").fetchall() == before
    finally:
        db.close()


def test_existing_26_column_research_rows_and_install_receipt_survive_mirroring():
    db = store()
    book = Book()
    bundle = db.export_mirror_bundle("SBER", "2026-10-12", "2026-10-12")
    try:
        old, _ = project_source(bundle, "H1", [])
        archive = deepcopy(old[1])
        archive[0] = "SBER|H1|SBER|2026-10-11T10:00:00+03:00"
        archive[1] = "2026-10-11"
        archive[4:6] = ["2026-10-11T10:00:00+03:00", "2026-10-11T10:59:59+03:00"]
        archive[20:] = ["RESEARCH_NOT_ADMITTED", "saved-hash", "archive-hash", "UNVERIFIED", "ARCHIVE", "PIT_NOT_ATTESTED"]
        book.data["H1"] = [old[0], archive]
        book.data["SYNC_STATUS"].append(["br_home_install_20261016_receipt", "PRESERVE"])
        result = HomeCanonicalMirror(db, book, "a"*40).sync(symbol="SBER", from_date="2026-10-12", till_date="2026-10-12", dry_run=False, exclusive_workbook_owner=True)
        assert result["status"] == "MIRROR_SYNC_PASS"
        assert book.data["H1"][1] == archive
        assert len(book.data["H1"][0]) == 26 and len(book.data["H1"]) == 3
        assert dict(book.data["SYNC_STATUS"][1:])["br_home_install_20261016_receipt"] == "PRESERVE"
    finally:
        db.close()


def test_legacy_google_serial_dates_match_without_rewriting_cells():
    from datetime import datetime
    db=store();bundle=db.export_mirror_bundle("SBER","2026-10-12","2026-10-12")
    old,_=project_source(bundle,"D1",[])
    start=(datetime(2026,10,12,10)-datetime(1899,12,30)).total_seconds()/86400
    end=(datetime(2026,10,12,23,59,59)-datetime(1899,12,30)).total_seconds()/86400
    old[1][4]=start;old[1][5]=end;old[1][6]=end
    old[1][1]=old[1][2]=int(start)
    expected=deepcopy(old)
    try:
        incoming,evidence=project_source(bundle,"D1",old)
        assert incoming==expected and old==expected and evidence["matched_source_bars"]==1
        assert isinstance(incoming[1][4],float)
        old[1][4]+=1
        with pytest.raises(MirrorConflict,match="record_key"):
            project_source(bundle,"D1",old)
    finally:db.close()


@pytest.mark.parametrize("value",[True,float("inf"),-1,100001,44200.123456])
def test_unsupported_numeric_timestamp_is_not_guessed(value):
    from birzha.application.canonical_market_mirror import sheet_timestamp
    with pytest.raises(MirrorConflict):sheet_timestamp(value)


def test_identical_source_space_T_aliases_reconcile_without_database_changes():
    db=store();bundle=db.export_mirror_bundle("SBER","2026-10-12","2026-10-12")
    alias=deepcopy(next(c for c in bundle["candles"] if c["timeframe"]=="M15"))
    alias["begin"]=alias["begin"].replace(" ","T")
    alias["end_time"]=alias["end_time"].replace(" ","T")
    bundle["candles"].append(alias)
    before=deepcopy(bundle)
    try:
        rows,evidence=project_source(bundle,"M15",[])
        assert len(rows)==2 and evidence["identical_source_time_aliases"]==1
        assert bundle==before
        alias["close"]+=0.1
        with pytest.raises(MirrorConflict,match="conflicting semantic"):
            project_source(bundle,"M15",[])
        alias["close"]-=0.1;alias["source"]="OTHER_PROVENANCE"
        with pytest.raises(MirrorConflict,match="conflicting semantic"):
            project_source(bundle,"M15",[])
    finally:db.close()


def test_gold_future_export_excludes_unrelated_exact_stock_security_and_sessions():
    db=DuckDBHistoricalCandleStore(":memory:")
    future=Instrument(symbol="GOLD",secid="GDZ6",root_symbol="GOLD",board="RFUD",engine="futures",market="forts",asset_class="future")
    stock=Instrument(symbol="GOLD",secid="GOLD",board="TQBR",engine="stock",market="shares",asset_class="equity")
    candle=Candle(open=100,high=102,low=99,close=101,volume=10,value=1000,begin="2026-10-12 10:00:00",end="2026-10-12 10:59:59")
    try:
        for instrument in (future,stock):
            db.upsert_series(CandleSeries(instrument,"H1",(candle,)))
            db.record_sessions("GOLD",instrument.secid,("2026-10-12",))
        before=db._connection.execute("SELECT * FROM historical_candles ORDER BY secid").fetchall()
        bundle=db.export_mirror_bundle("GOLD","2026-10-12","2026-10-12")
        assert [r["secid"] for r in bundle["candles"]]==["GDZ6"]
        assert bundle["sessions"]==[["GOLD","GDZ6","2026-10-12"]]
        assert db._connection.execute("SELECT * FROM historical_candles ORDER BY secid").fetchall()==before
    finally:db.close()


def test_stricter_archive_completion_is_retained_and_reported_not_promoted():
    db=store();bundle=db.export_mirror_bundle("SBER","2026-10-12","2026-10-12")
    old,_=project_source(bundle,"H1",[]);old[1][16]=False
    try:
        projected,evidence=project_source(bundle,"H1",old)
        assert projected==old and projected[1][16] is False
        assert evidence["preserved_stricter_completion"]==1
        assert evidence["full_source_payload_match_claimed"] is False
        old[1][16]=True
        next(c for c in bundle["candles"] if c["timeframe"]=="H1")["completed"]=False
        with pytest.raises(MirrorConflict,match="source completion"):
            project_source(bundle,"H1",old)
    finally:db.close()


@pytest.mark.parametrize("symbol,allowed",[("RTSI",True),("IMOEX",True),("SBER",False)])
def test_missing_index_volume_is_preserved_never_equated_to_zero(symbol,allowed):
    db=store(symbol);bundle=db.export_mirror_bundle(symbol,"2026-10-12","2026-10-12")
    old,_=project_source(bundle,"D1",[]);old[1][13]=""
    candle=next(c for c in bundle["candles"] if c["timeframe"]=="D1");candle["volume"]=0.
    try:
        if allowed:
            projected,evidence=project_source(bundle,"D1",old)
            assert projected==old and projected[1][13]==""
            assert evidence["preserved_unknown_index_volume"]==1
            candle["close"]+=0.1
            with pytest.raises(MirrorConflict,match="candle values"):
                project_source(bundle,"D1",old)
        else:
            with pytest.raises(MirrorConflict,match="candle values"):
                project_source(bundle,"D1",old)
    finally:db.close()
