"""Offline acceptance for RI 2025 chronological indicator backfill."""
from copy import deepcopy
from datetime import datetime
import json

import httpx
import pytest

from birzha.application.archive_schema import COMPACT_CANDLE_COLUMNS
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.application.google_first_archive import GoogleFirstArchive, MSK
from birzha.application.model3_indicator_coverage import COMPACT_RI_INDICATOR_COLUMNS
from birzha.application.model3_google_backfill import plan_ri_indicator_backfill
from birzha.storage.google_sheets_bridge import INSTRUMENT_FOLDER_IDS, INSTRUMENT_TARGETS
from birzha.storage.native_canonical_sheets import NativeCanonicalSheets


def candles():
    h = list(COMPACT_CANDLE_COLUMNS)
    source = [h]
    for day, secid, price in (
        ("2025-01-03", "RIH5", 100),
        ("2025-01-06", "RIH5", 110),
        ("2026-01-05", "RIH6", 130),
    ):
        start = day + "T00:00:00+03:00"
        end = day + "T23:59:59+03:00"
        key = "RI|D1|" + secid + "|" + start
        v = dict(record_key=key, contract_code=secid, calendar_date=day,
                 bar_start_time=start, bar_end_time=end, open=price,
                 high=price+10, low=price-5, close=price+1, volume=100,
                 is_complete=True, normalized_sha256="sha_" + day,
                 research_status="RECONSTRUCTED_RESEARCH_ONLY")
        source.append([v.get(k, "") for k in h])
    return source


def historical_indicators(source):
    h = list(COMPACT_RI_INDICATOR_COLUMNS)
    row = [""] * len(h)
    def put(k,v):row[h.index(k)] = v
    put("record_key", "RI|RIH6|D1|2026-01-05T00:00:00+03:00|P3_OBS_V1")
    put("source_record_key", source[-1][0])
    put("source_record_sha256", "sha_2026-01-05")
    put("begin", "2026-01-05T00:00:00+03:00")
    put("end", "2026-01-05T23:59:59+03:00")
    put("secid", "RIH6")
    put("symbol", "RI")
    put("timeframe", "D1")
    put("p3_parameters_version", "P3_OBS_FEATURES_V1_CONFIG_20261030")
    return [h, row]


def test_plan_backfill_reuses_research_formulas_and_keeps_2026_unchanged():
    source = candles()
    before = historical_indicators(source)
    plan, rows = plan_ri_indicator_backfill(
        candles=source, daily=source, existing=before, timeframe="D1",
        from_date="2025-01-01", till_date="2025-01-31", max_rows=50,
        calculated_at="2026-11-01T15:00:00+00:00")
    h=before[0]
    assert plan["status"] == "MODEL3_RESEARCH_BACKFILL_PLAN"
    assert plan["planned_rows"] == 2
    assert plan["insert_row_index"] == 1
    assert [r[h.index("source_record_key")] for r in rows] == [source[1][0], source[2][0]]
    assert all(r[h.index("p3_signal_eligible")] is False for r in rows)
    assert all(r[h.index("calendar_status")] == "CALENDAR_UNVERIFIED" for r in rows)
    assert all(r[h.index("indicator_status")].startswith("RESEARCH_NOT_SIGNAL") for r in rows)
    assert before == historical_indicators(source)


class Book:
    def __init__(self):
        self.source = candles()
        self.features = historical_indicators(self.source)
        self.writes = 0
        self.status = {}
        self.fail_after_commit = False
    def validate_model3_target(self, s, sheet):
        assert (s, sheet) == ("RI","D1_INDICATORS")
    def read_rows(self, s, sheet):
        assert (s,sheet) == ("RI","D1_INDICATORS")
        return deepcopy(self.features)
    def model3_insert_rows(self, s, sheet, index, rows):
        self.writes += 1
        self.features[index:index] = deepcopy(rows)
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise RuntimeError("unknown reply after atomic commit")
    def update_status(self,s,u):self.status.update(u)


def _service(book, writer):
    service=GoogleFirstArchive(book, None, "a"*40, sole_writer=writer,
                               now=lambda: datetime(2026,11,1,tzinfo=MSK))
    service._schemas=lambda symbol,tf: deepcopy(book.source)
    return service


def test_no_write_without_explicit_owner_and_sole_writer():
    book=Book()
    args=dict(timeframe="D1",from_date="2025-01-01",till_date="2025-01-31",max_rows=1)
    result=_service(book,False).model3_backfill(**args,dry_run=True)
    assert result["planned_rows"]==1 and book.writes==0
    with pytest.raises(MirrorConflict,match="OWNER_AND_SOLE_WRITER"):
        _service(book,False).model3_backfill(**args,dry_run=False,owner_confirmed=True)
    with pytest.raises(MirrorConflict,match="OWNER_AND_SOLE_WRITER"):
        _service(book,True).model3_backfill(**args,dry_run=False,owner_confirmed=False)
    assert book.features[1][book.features[0].index("begin")].startswith("2026-")
    assert book.writes==0


def test_chronological_insert_readback_and_manual_repeat_safe():
    book=Book()
    service=_service(book,True)
    args=dict(timeframe="D1",from_date="2025-01-01",till_date="2025-01-31",max_rows=1,
              dry_run=False,owner_confirmed=True)
    a=service.model3_backfill(**args)
    b=service.model3_backfill(**args)
    c=service.model3_backfill(**args)
    assert a["status"]=="READBACK_PASS_RESEARCH_ONLY"
    assert b["status"]=="READBACK_PASS_RESEARCH_ONLY"
    assert c["status"]=="MODEL3_NO_MISSING_IN_WINDOW"
    assert book.writes==2
    header=book.features[0]
    ordered=[r[header.index("begin")][:10] for r in book.features[1:]]
    assert ordered==["2025-01-03","2025-01-06","2026-01-05"]
    assert book.features[-1]==historical_indicators(book.source)[-1]
    assert book.status["model3_google_backfill_status"]=="READBACK_PASS_RESEARCH_ONLY"


def test_lost_response_never_retries_and_existing_source_key_reconciles():
    book=Book()
    book.fail_after_commit=True
    service=_service(book,True)
    args=dict(timeframe="D1",from_date="2025-01-03",till_date="2025-01-03",max_rows=1,
              dry_run=False,owner_confirmed=True)
    with pytest.raises(MirrorConflict,match="UNKNOWN_RECONCILE"):
        service.model3_backfill(**args)
    assert book.writes==1
    verified=service.model3_backfill(**{**args,"dry_run":True})
    assert verified["status"]=="MODEL3_NO_MISSING_IN_WINDOW"
    assert book.writes==1
    assert len(book.features)==3


def test_fail_closed_on_formula_and_unsorted_existing_rows():
    book=Book()
    row=book.features[1]
    row[book.features[0].index("atr_14_wilder")]="=SUM(1,2)"
    with pytest.raises(MirrorConflict,match="FORMULAS"):
        _service(book,True).model3_backfill(timeframe="D1",from_date="2025-01-01",till_date="2025-01-31")
    book=Book()
    old=book.features[1][:]
    old[book.features[0].index("begin")]="2024-01-01T00:00:00+03:00"
    book.features.append(old)
    with pytest.raises((MirrorConflict,ValueError)):
        _service(book,True).model3_backfill(timeframe="D1",from_date="2025-01-01",till_date="2025-01-31")


def test_native_atomic_insert_has_no_clear_update_old_or_auto_retry():
    target=INSTRUMENT_TARGETS["RI"]
    submitted=[]
    def handler(req):
        if "/drive/" in str(req.url):
            return httpx.Response(200,json={"id":target.spreadsheet_id,"name":target.spreadsheet_name,
                "mimeType":"application/vnd.google-apps.spreadsheet","trashed":False,
                "parents":[INSTRUMENT_FOLDER_IDS["RI"]]})
        if req.method=="POST":
            submitted.append(json.loads(req.content))
            return httpx.Response(200,json={})
        return httpx.Response(200,json={"spreadsheetId":target.spreadsheet_id,"sheets":[{
            "properties":{"sheetId":54321,"title":"D1_INDICATORS","sheetType":"GRID",
                          "gridProperties":{"rowCount":1000,"columnCount":95}}}]})
    t=NativeCanonicalSheets(lambda:"test-token",transport=httpx.MockTransport(handler))
    sample=[[""]*95]
    t.model3_insert_rows("RI","D1_INDICATORS",1,sample)
    assert len(submitted)==1
    reqs=submitted[0]["requests"]
    assert list(reqs[0])==["insertDimension"]
    assert reqs[0]["insertDimension"]["range"]["startIndex"]==1
    assert list(reqs[1])==["updateCells"]
    assert reqs[1]["updateCells"]["start"]["rowIndex"]==1
    assert not any("delete" in json.dumps(x).lower() or "clear" in json.dumps(x).lower() for x in reqs)
    with pytest.raises(MirrorConflict,match="COMPACT_ROWS"):
        t.model3_insert_rows("RI","D1_INDICATORS",1,[[""]*94])


def test_m1_zip_proxy_uses_exact_sec_month_and_labels_approximation():
    from hashlib import sha256
    from birzha.storage.google_m1_bytes import merge_zip
    from birzha.application.model3_google_backfill import minute_features_from_existing_drive
    sec="RIH5";date="2025-01-03"
    raw=[{
        "begin":f"{date}T10:00:00+03:00","end":f"{date}T10:00:59+03:00",
        "open":100,"high":102,"low":99,"close":101,"volume":50,"value":None},
        {
        "begin":f"{date}T10:01:00+03:00","end":f"{date}T10:01:59+03:00",
        "open":101,"high":103,"low":100,"close":102,"volume":50,"value":None}]
    body,changed=merge_zip(None,raw,"RI",sec,"2025-01")
    assert changed
    class Cloud:
        def __init__(self):self.calls=[]
        def read_m1(self,s,contract,month):
            self.calls.append((s,contract,month))
            return dict(body=body,sha256=sha256(body).hexdigest(),
                        file_id="existing-pinned-file",indexed=True)
    source=candles()
    h=list(COMPACT_CANDLE_COLUMNS)
    t=f"{date}T10:00:00+03:00"
    candle=dict(record_key=f"RI|H1|{sec}|{t}",contract_code=sec,
                calendar_date=date,bar_start_time=t,
                bar_end_time=f"{date}T10:59:59+03:00",
                open=100,high=103,low=99,close=102,volume=100,
                is_complete=True,normalized_sha256="source-sha")
    table=[h,[candle.get(x,"") for x in h]]
    cloud=Cloud()
    profiles,evidence=minute_features_from_existing_drive(
        transport=cloud,candles=table,timeframe="H1",
        source_keys={candle["record_key"]},till_date=date)
    assert cloud.calls==[("RI","RIH5","2025-01")]
    assert len(evidence)==1 and evidence[0]["sha256"]==sha256(body).hexdigest()
    key=(sec,"H1",f"{date} 10:00:00")
    assert profiles[key]["volume"]==100
    assert profiles[key]["count"]==2
    assert profiles[key]["delta"]==50  # proxy: minute-close sign, NOT aggressor delta
    existing=[list(COMPACT_RI_INDICATOR_COLUMNS)]
    plan,rows=plan_ri_indicator_backfill(
        candles=table,daily=source,existing=existing,timeframe="H1",
        from_date=date,till_date=date,max_rows=50,
        calculated_at="2026-11-01T15:00:00+00:00",minute_features=profiles)
    ih=existing[0]
    assert plan["minute_features"]=="APPROX_SOURCE_M1"
    assert rows[0][ih.index("p3_minute_count")]==2
    assert rows[0][ih.index("p3_delta_proxy_status")].startswith("M1_CLOSE_SIGN")
    assert rows[0][ih.index("p3_minute_sign_delta_proxy")]==50
    assert rows[0][ih.index("p3_profile_status")].endswith("APPROX")
    assert rows[0][ih.index("p3_signal_eligible")] is False
    wrong=[list(x) for x in table]
    wrong[1][h.index("volume")]=99
    with pytest.raises(MirrorConflict,match="VOLUME_CONFLICT"):
        minute_features_from_existing_drive(
            transport=cloud,candles=wrong,timeframe="H1",
            source_keys={candle["record_key"]},till_date=date)


def test_minute_profiles_require_indexed_existing_source():
    from birzha.application.model3_google_backfill import minute_features_from_existing_drive
    table=candles()
    with pytest.raises(MirrorConflict,match="INTRADAY"):
        minute_features_from_existing_drive(
            transport=None,candles=table,timeframe="D1",
            source_keys={table[1][0]},till_date="2025-01-03")
