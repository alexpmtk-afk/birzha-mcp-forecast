from copy import deepcopy
import json
import pytest
import httpx
from birzha.application.market_mirror import D1_COLUMNS
from birzha.application.canonical_market_mirror import CanonicalMirrorPublisher, MirrorConflict, plan_append
from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS, INSTRUMENT_FOLDER_IDS

HEADER = D1_COLUMNS + ["research_status", "caveat"]

def rows(day="2025-01-03", symbol="SBER", tf="H1", complete=True):
    start = f"{day}T10:00:00+03:00"
    end = f"{day}T10:59:59+03:00"
    code = {"SI": "SiH5", "BR": "BRH5", "GOLD": "GDH5"}.get(symbol, symbol)
    return [HEADER[:], [f"{symbol}|{tf}|{code}|{start}",day,"","",start,end,"","UNKNOWN",code,100,102,99,101,10,"","",complete,"H1",1,"MOEX_ISS","RECONSTRUCTED_RESEARCH","PIT_NOT_ATTESTED"]]

class Memory:
    def __init__(self, data): self.data=deepcopy(data); self.writes=0; self.failure=None; self.reads=0
    def validate_target(self, symbol, timeframe): pass
    def read_rows(self, symbol, timeframe):
        self.reads += 1
        if self.failure == "competing" and self.reads == 2: self.data[1][9] = 100.5
        return deepcopy(self.data)
    def append_rows(self, symbol, timeframe, incoming):
        self.writes += 1; self.data += deepcopy(incoming)
        if self.failure == "lost": raise MirrorConflict("lost response")
        if self.failure == "corrupt": self.data[-1][12] = 100.5

@pytest.mark.parametrize("symbol", INSTRUMENT_TARGETS)
@pytest.mark.parametrize("tf", ["D1", "H1", "M15"])
def test_all_markets_timeframes_append_preserve_unknown_and_partial(symbol, tf):
    old=rows(symbol=symbol, tf=tf); new=rows("2025-01-04",symbol,tf,False)
    transport=Memory(old); publisher=CanonicalMirrorPublisher(transport)
    result=publisher.sync(symbol=symbol,timeframe=tf,incoming=new,exclusive_workbook_owner=True)
    assert result["added_rows"] == 1
    assert transport.data[:2] == old
    assert transport.data[-1][2:4] == ["", ""]
    assert transport.data[-1][6:8] == ["", "UNKNOWN"]
    assert transport.data[-1][16] is False
    again=publisher.sync(symbol=symbol,timeframe=tf,incoming=new,exclusive_workbook_owner=True)
    assert again["added_rows"] == 0 and transport.writes == 1

@pytest.mark.parametrize("fault", ["price", "duplicate", "wrong_symbol", "wrong_tf", "empty_key", "bool_price", "nan", "schema", "formula", "volume", "causal_promotion", "session", "naive_time"])
def test_negative_preflight_never_writes(fault):
    old=rows(); new=rows("2025-01-04")
    if fault == "price": new=deepcopy(old); new[1][12]=100.5
    elif fault == "duplicate": new.append(new[1][:])
    elif fault == "wrong_symbol": new=rows("2025-01-04","IMOEX")
    elif fault == "wrong_tf": new=rows("2025-01-04",tf="M15")
    elif fault == "empty_key": new[1][0]=""
    elif fault == "bool_price": new[1][9]=True
    elif fault == "nan": new[1][9]=float("nan")
    elif fault == "schema": new[0][-1]="other"
    elif fault == "formula": new[1][19]="=HYPERLINK(1)"
    elif fault == "volume": new[1][13]=-1
    elif fault == "causal_promotion": new[1][7]="VERIFIED"
    elif fault == "session": new[1][2]="2025-01-04"
    elif fault == "naive_time": new[1][4]="2025-01-04 10:00:00"; new[1][0]="SBER|H1|SBER|2025-01-04 10:00:00"
    transport=Memory(old)
    with pytest.raises(MirrorConflict): CanonicalMirrorPublisher(transport).sync(symbol="SBER",timeframe="H1",incoming=new,exclusive_workbook_owner=True)
    assert transport.writes == 0 and transport.data == old

@pytest.mark.parametrize("failure", ["lost", "corrupt", "competing"])
def test_failure_never_returns_pass_and_lost_response_resumes(failure):
    transport=Memory(rows()); transport.failure=failure
    new=rows("2025-01-04")
    publisher=CanonicalMirrorPublisher(transport)
    with pytest.raises(MirrorConflict): publisher.sync(symbol="SBER",timeframe="H1",incoming=new,exclusive_workbook_owner=True)
    if failure == "lost":
        transport.failure=None
        result=publisher.sync(symbol="SBER",timeframe="H1",incoming=new,exclusive_workbook_owner=True)
        assert result["added_rows"] == 0 and transport.writes == 1
    elif failure == "competing": assert transport.writes == 0


def test_no_owner_blocks_before_read():
    transport=Memory(rows())
    with pytest.raises(MirrorConflict): CanonicalMirrorPublisher(transport).sync(symbol="SBER",timeframe="H1",incoming=rows())
    assert transport.reads == transport.writes == 0


def test_large_m15_not_limited_to_25000():
    header=rows(tf="M15")[0]; batch=[header]
    for i in range(33486):
        from datetime import datetime, timedelta
        row=rows(tf="M15")[1]
        start=datetime.fromisoformat("2025-01-03T10:00:00+03:00")+timedelta(minutes=15*i)
        row[4]=start.isoformat(); row[5]=(start+timedelta(minutes=15,seconds=-1)).isoformat()
        row[0]=f"SBER|M15|SBER|{row[4]}"; batch.append(row)
    # No-op overlap validates preserved evidence without reinterpreting old timestamps.
    plan=plan_append(symbol="SBER",timeframe="M15",existing=batch,incoming=batch)
    assert len(plan.before)==33487 and plan.additions==[]

@pytest.mark.parametrize("status,confidence", [("INDIVIDUAL_BAR_VALID_SESSION_UNCERTIFIED","NOT_PIT_VERIFIED"),("RESEARCH_ONLY_STRICT","INFERRED"),("RESEARCH_ONLY_SOURCE_PARTIAL","INFERRED")])
def test_actual_research_labels_not_promoted(status, confidence):
    new=rows("2025-01-04"); new[1][20]=status; new[1][7]=confidence
    if confidence == "INFERRED": new[1][6]=new[1][5]
    plan=plan_append(symbol="SBER",timeframe="H1",existing=rows(),incoming=new)
    assert plan.additions == new[1:]


def test_schema_difference_is_explicit_not_silent_column_loss():
    new=rows("2025-01-04"); new[0].append("source_file_id"); new[1].append("old-source")
    with pytest.raises(MirrorConflict,match="schema mismatch"): plan_append(symbol="SBER",timeframe="H1",existing=rows(),incoming=new)


def native(data, fault=None):
    state={"data": deepcopy(data), "posts": [], "requests": []}
    target=INSTRUMENT_TARGETS["SBER"]
    def handler(request):
        state["requests"].append(request)
        assert request.headers["Authorization"] == "Bearer dummy-not-a-secret"
        if request.url.host == "www.googleapis.com":
            meta={"id":target.spreadsheet_id,"name":target.spreadsheet_name,"mimeType":"application/vnd.google-apps.spreadsheet","parents":[INSTRUMENT_FOLDER_IDS["SBER"]],"trashed":False}
            if fault == "folder": meta["parents"]=["wrong"]
            if fault == "name": meta["name"]="old-name"
            if fault == "403": return httpx.Response(403,json={"error":"do not echo sensitive body"})
            return httpx.Response(200,json=meta)
        if request.method == "POST":
            body=json.loads(request.content); state["posts"].append(body)
            append=body["requests"][0]["appendCells"]
            assert append["sheetId"]==42 and append["fields"] == "userEnteredValue"
            for row in append["rows"]:
                state["data"].append([next(iter(x["userEnteredValue"].values())) if "userEnteredValue" in x else "" for x in row["values"]])
            if fault == "lost": raise httpx.ReadTimeout("sensitive transport text")
            return httpx.Response(200,json={"replies":[{}]})
        if "/values/" in request.url.path:
            assert request.url.params["valueRenderOption"] == "FORMULA"
            return httpx.Response(200,json={"values":state["data"]})
        sheets=[] if fault == "tab" else [{"properties":{"sheetId":42,"title":"H1","sheetType":"GRID","gridProperties":{"rowCount":max(100,len(state["data"])),"columnCount":22}}}]
        return httpx.Response(200,json={"spreadsheetId":target.spreadsheet_id,"sheets":sheets})
    return NativeCanonicalSheets(lambda: "dummy-not-a-secret",transport=httpx.MockTransport(handler)),state


def test_native_end_to_end_uses_only_fixed_ids_and_literal_append():
    transport,state=native(rows()); publisher=CanonicalMirrorPublisher(transport)
    assert publisher.sync(symbol="SBER",timeframe="H1",incoming=rows("2025-01-04"),exclusive_workbook_owner=True)["added_rows"]==1
    assert state["data"] == rows()+rows("2025-01-04")[1:]
    assert len(state["posts"])==1
    assert all(INSTRUMENT_TARGETS["SBER"].spreadsheet_id in str(x.url) for x in state["requests"])
    assert all(set(r)=={"appendCells"} for b in state["posts"] for r in b["requests"])

@pytest.mark.parametrize("fault", ["folder","name","403","tab"])
def test_native_refusal_no_create_or_write(fault):
    transport,state=native(rows(),fault)
    with pytest.raises(MirrorConflict): CanonicalMirrorPublisher(transport).sync(symbol="SBER",timeframe="H1",incoming=rows("2025-01-04"),exclusive_workbook_owner=True)
    assert not state["posts"]


def test_native_lost_post_no_retry_no_sensitive_exception_text():
    transport,state=native(rows(),"lost")
    with pytest.raises(MirrorConflict) as exc: CanonicalMirrorPublisher(transport).sync(symbol="SBER",timeframe="H1",incoming=rows("2025-01-04"),exclusive_workbook_owner=True)
    assert len(state["posts"]) == 1
    assert "sensitive" not in str(exc.value)

def test_integral_number_representation_does_not_change_readback_hash():
    from birzha.application.canonical_market_mirror import digest
    assert digest([[100.0, True]]) == digest([[100, True]])
    assert digest([[1, True]]) != digest([[True, True]])


def test_existing_duplicate_keys_block_before_write():
    bad=rows(); bad.append(bad[1][:]); transport=Memory(bad)
    with pytest.raises(MirrorConflict): CanonicalMirrorPublisher(transport).sync(symbol="SBER", timeframe="H1", incoming=rows("2025-01-04"), exclusive_workbook_owner=True)
    assert transport.writes == 0


def test_empty_existing_tab_initialises_header_from_validated_source_without_new_book():
    transport=Memory([])
    result=CanonicalMirrorPublisher(transport).sync(symbol="SBER", timeframe="H1", incoming=rows(), exclusive_workbook_owner=True)
    assert result["added_rows"] == result["data_rows"] == 1
    assert transport.data == rows() and transport.writes == 1


def test_more_than_2000_new_rows_refused_before_first_write():
    from datetime import datetime, timedelta
    source=[HEADER[:]]
    for i in range(2001):
        row=rows()[1]
        start=datetime.fromisoformat("2025-01-04T10:00:00+03:00")+timedelta(hours=i)
        row[4]=start.isoformat(); row[5]=(start+timedelta(hours=1,seconds=-1)).isoformat()
        row[0]=f"SBER|H1|SBER|{row[4]}"; source.append(row)
    transport=Memory(rows())
    with pytest.raises(MirrorConflict, match="2000 additions"): CanonicalMirrorPublisher(transport).sync(symbol="SBER",timeframe="H1", incoming=source,exclusive_workbook_owner=True)
    assert transport.writes == 0


def test_legacy_sync_cannot_stage_or_overwrite_canonical_history(monkeypatch):
    from types import SimpleNamespace
    import birzha.application.market_mirror_sync as module
    from birzha.application.market_mirror_sync import MarketMirrorSyncService
    from birzha.storage.google_sheets_bridge import GoogleSheetsBridge, GoogleSheetsBridgeConfig, GoogleSheetsBridgeError
    bridge=GoogleSheetsBridge(GoogleSheetsBridgeConfig("https://script.google.com/macros/s/test/exec", "dummy", "dummy-root"))
    monkeypatch.setattr(bridge,"health",lambda: {})
    monkeypatch.setattr(bridge,"ensure_archive",lambda **k: {"spreadsheet_id": INSTRUMENT_TARGETS["SBER"].spreadsheet_id})
    monkeypatch.setattr(bridge,"_post",lambda *a, **k: pytest.fail("no staging or write"))
    monkeypatch.setattr(module,"build_market_mirror_snapshot",lambda *a, **k: SimpleNamespace(symbol="SBER", sheets={"D1": rows(tf="D1"), "SESSIONS": [["s"]], "VERIFIED_RANGES": [["v"]]}))
    with pytest.raises(GoogleSheetsBridgeError,match="append-only"):
        MarketMirrorSyncService(object(),bridge).sync(symbol="SBER",from_date="2025-01-03",till_date="2025-01-03")


def test_authorisation_provider_error_does_not_expose_secret():
    def broken(): raise RuntimeError("secret example")
    transport=NativeCanonicalSheets(broken,transport=httpx.MockTransport(lambda req: pytest.fail("no request")))
    with pytest.raises(MirrorConflict) as exc: transport.validate_target("SBER","H1")
    assert "secret example" not in str(exc.value)


def test_native_payload_bound_before_post():
    transport,state=native(rows()); transport.validate_target("SBER","H1")
    row=rows("2025-01-04")[1]; row[19]="x"*2000; row[21]="x"*2000
    with pytest.raises(MirrorConflict,match="1 MB"): transport.append_rows("SBER","H1",[row[:] for _ in range(500)])
    assert state["posts"] == []