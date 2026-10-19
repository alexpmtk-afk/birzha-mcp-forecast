"""Exercise actual Google API request shapes through NativeCanonicalSheets MockTransport."""
from copy import deepcopy
from urllib.parse import unquote
import re

import httpx
import pytest

from birzha.application.m1_named_fields import SchemaError
from birzha.storage.google_sheets_bridge import INSTRUMENT_TARGETS
from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
from birzha.storage.m1_named_google_sheets import M1NamedGoogleSheets


HEADERS = [
    "indicator_status", "record_key", "source_record_key", "atr_14_wilder",
    "atr_formula_id", "atr_source_record_key", "source_record_sha256", "timeframe",
    "formula_id", "atr_14_wilder_status", "secid",
]


def mock_api(*, canonical=False, shuffled=False, blank_row=False, duplicate=False,
             partial=False, lost_ack=False, conflicting_read=False, formula=False):
    title = ("MOEX_HISTDATA_SBER_MCP_CANONICAL" if canonical else "M1 TEST COPY")
    book_id = (INSTRUMENT_TARGETS["SBER"].spreadsheet_id if canonical else "TEST_COPY_NOT_CANONICAL")
    names = list(reversed(HEADERS)) if shuffled else list(HEADERS)
    if duplicate:
        names.append("record_key")
    def record(k, status):
        d = {name: "" for name in names}
        d.update(record_key=k, secid="SBER", timeframe="D1",
                 source_record_key="SBER|D1|SBER|2026-01-01",
                 source_record_sha256="synthetic-hash", formula_id="research",
                 atr_14_wilder=5.0, atr_14_wilder_status="WARMUP",
                 atr_formula_id="m1_atr14_wilder",
                 atr_source_record_key="SBER|D1|SBER|2026-01-01",
                 indicator_status=status)
        if formula:
            d["indicator_status"] = '=IF(TRUE,"WARMUP","BAD")'
        return [d.get(name,"") for name in names]
    data = [names, record("A","PENDING"), record("B","PENDING")]
    if blank_row:
        data = [names, record("A","PENDING"), [], record("B","PENDING")]
    state = {"rows":data,"posts":[], "reads":0}
    def handler(request):
        url = str(request.url)
        path = unquote(request.url.path)
        if request.method == "GET" and request.url.host == "www.googleapis.com":
            return httpx.Response(200,json={"id":book_id,"name":title,
                "mimeType":"application/vnd.google-apps.spreadsheet","trashed":False})
        if request.method == "GET" and path.endswith("/spreadsheets/"+book_id):
            return httpx.Response(200,json={"spreadsheetId":book_id,"sheets":[{"properties":{
                "sheetId":123,"title":"D1_INDICATORS","sheetType":"GRID",
                "gridProperties":{"rowCount":max(8,len(state["rows"])+1),"columnCount":len(names)}}}]})
        if request.method == "GET" and "/values/" in path:
            address=path.split("/values/",1)[1]
            match=re.fullmatch(r"'D1_INDICATORS'!([A-Z]+)(\d+):([A-Z]+)(\d+)",address)
            assert match, address
            start,end=int(match.group(2)),int(match.group(4))
            result=deepcopy(state["rows"][start-1:end])
            while result and result[-1] == []:
                result.pop()
            state["reads"]+=1
            if conflicting_read and state["reads"]>=4 and start>=2:
                if len(result)>=1:
                    result[0][0]="EXTERNALLY_CHANGED"
            return httpx.Response(200,json={"values":result})
        if request.method == "POST":
            import json
            body=json.loads(request.content)
            state["posts"].append(body)
            if not canonical:
                edits=[x["updateCells"] for x in body["requests"]]
                if partial: edits=edits[:1]
                for edit in edits:
                    at=edit["start"]
                    new=edit["rows"][0]["values"][0]["userEnteredValue"]
                    state["rows"][at["rowIndex"]][at["columnIndex"]]=new.get("stringValue")
            if lost_ack:
                return httpx.Response(503,json={"error":"lost response after commit"})
            return httpx.Response(200,json={"replies":[{}]})
        return httpx.Response(404)
    trans=NativeCanonicalSheets(lambda:"synthetic-token",transport=httpx.MockTransport(handler))
    client=M1NamedGoogleSheets(trans,spreadsheet_id=book_id,expected_title=title,
                              sheet="D1_INDICATORS")
    return client,state


def test_read_live_api_uses_actual_headers():
    client,state=mock_api(shuffled=True)
    data=client.read()
    assert data.schema.position("record_key") != 1
    assert data.records()[0]["record_key"]=="A"
    assert state["reads"]>0
    assert state["posts"]==[]


def test_permuted_columns_write_to_actual_field_not_position():
    client,state=mock_api(shuffled=True)
    result=client.patch_test_copy({"B":{"indicator_status":"VERIFIED"}},isolated_copy_approved=True)
    assert result == {"status":"TEST_COPY_BACKREAD_PASS","updated_cells":1}
    assert state["rows"][2][state["rows"][0].index("indicator_status")]=="VERIFIED"
    assert state["rows"][2][state["rows"][0].index("record_key")]=="B"
    request=state["posts"][0]["requests"][0]["updateCells"]
    assert request["start"]["columnIndex"]==state["rows"][0].index("indicator_status")


def test_no_change_does_not_send_post():
    client,state=mock_api()
    r=client.patch_test_copy({"A":{"indicator_status":"PENDING"}},isolated_copy_approved=True)
    assert r["status"]=="NO_CHANGE"
    assert state["posts"]==[]


def test_canonical_write_refused_even_with_approval():
    client,state=mock_api(canonical=True)
    assert client.read().records()[0]["record_key"]=="A"
    with pytest.raises(SchemaError,match="CANONICAL_WRITE_FORBIDDEN"):
        client.patch_test_copy({"A":{"indicator_status":"VERIFIED"}},
                               isolated_copy_approved=True)
    assert state["posts"]==[]


def test_no_approval_no_write():
    client,state=mock_api()
    with pytest.raises(SchemaError,match="test-copy approval"):
        client.patch_test_copy({"A":{"indicator_status":"VERIFIED"}},
                               isolated_copy_approved=False)
    assert state["posts"]==[]


def test_duplicate_header_rejected_before_post():
    client,state=mock_api(duplicate=True)
    with pytest.raises(SchemaError,match="duplicated header"):
        client.patch_test_copy({"A":{"indicator_status":"VERIFIED"}},
                               isolated_copy_approved=True)
    assert state["posts"]==[]


def test_no_row_creation_allowed():
    client,state=mock_api()
    with pytest.raises(SchemaError,match="target key missing"):
        client.patch_test_copy({"C":{"indicator_status":"VERIFIED"}},
                               isolated_copy_approved=True)
    assert state["posts"]==[]


def test_only_status_fields_can_be_written():
    client,state=mock_api()
    with pytest.raises(SchemaError,match="only explicitly allowed"):
        client.patch_test_copy({"A":{"atr_14_wilder": 100}},
                               isolated_copy_approved=True)
    assert state["posts"]==[]


def test_formula_cells_cannot_be_overwritten():
    client,state=mock_api(formula=True)
    with pytest.raises(SchemaError,match="overwrite formula"):
        client.patch_test_copy({"A":{"indicator_status":"VERIFIED"}},
                               isolated_copy_approved=True)
    assert state["posts"]==[]


def test_partial_commit_detected_by_backread_no_auto_retry():
    client,state=mock_api(partial=True)
    with pytest.raises(SchemaError,match="content mismatch"):
        client.patch_test_copy({"A":{"indicator_status":"NEW","atr_14_wilder_status":"NEW"}},
                               isolated_copy_approved=True)
    assert len(state["posts"]) == 1


def test_lost_ack_after_commit_never_blind_retry():
    client,state=mock_api(lost_ack=True)
    with pytest.raises(Exception,match="HTTP 503"):
        client.patch_test_copy({"A":{"indicator_status":"VERIFIED"}},
                               isolated_copy_approved=True)
    assert len(state["posts"]) == 1
    assert state["rows"][1][state["rows"][0].index("indicator_status")]=="VERIFIED"


def test_blank_physical_row_must_not_shift_update_target():
    client,state=mock_api(blank_row=True)
    r=client.patch_test_copy({"B":{"indicator_status":"VERIFIED"}},
                             isolated_copy_approved=True)
    assert r["status"]=="TEST_COPY_BACKREAD_PASS"
    assert state["rows"][3][state["rows"][0].index("record_key")]=="B"
    assert state["rows"][3][state["rows"][0].index("indicator_status")]=="VERIFIED"


def test_fail_closed_when_external_change_during_preflight():
    client,state=mock_api(conflicting_read=True)
    with pytest.raises(SchemaError,match="sheet changed after preflight"):
        client.patch_test_copy({"B":{"indicator_status":"VERIFIED"}},
                               isolated_copy_approved=True)
    assert state["posts"]==[]


def test_bad_sheet_scope_rejected():
    client,state=mock_api()
    with pytest.raises(SchemaError,match="only canonical indicator"):
        M1NamedGoogleSheets(client.transport,spreadsheet_id="TEST_COPY_NOT_CANONICAL",
                            expected_title="M1 TEST COPY",sheet="FLOW")


def test_partial_requests_never_change_raw_source():
    client,state=mock_api()
    before=deepcopy(state["rows"])
    client.patch_test_copy({"A":{"indicator_status":"VERIFIED"}},isolated_copy_approved=True)
    assert state["rows"][1][state["rows"][0].index("atr_14_wilder")] == before[1][before[0].index("atr_14_wilder")]
    assert state["rows"][2]==before[2]
