"""Tests for model-independent collection without external requests."""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
import threading
import pytest
from test_model3_archive import Book
from birzha.application.archive_collection import ArchiveCollector
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.domain.market import Instrument,Candle,CandleSeries

class Market:
    def __init__(self):
        self.calls=0
        self.instrument=Instrument(symbol="RI",root_symbol="RI",secid="RIZ6",engine="futures",market="forts",board="RFUD",asset_class="future")
        self.direct_resolver=SimpleNamespace(resolve=lambda sec:self.instrument)
    def candles_for_instrument(self,ins,**kw):
        self.calls+=1
        return CandleSeries(ins,kw["timeframe"],(Candle(open=100,high=105,low=99,close=102,volume=12,value=None,
            begin="2026-10-29T10:00:00+03:00",end="2026-10-29T10:59:59+03:00",completed=True),))

def collector(tmp_path):
    b=Book(n=1);b.validate_target=lambda symbol,tf:b.validate_model3_target(symbol,tf)
    b.data["H1"]=[b.data["H1"][0]]
    m=Market();return b,m,ArchiveCollector(b,m,str(tmp_path),threading.Lock())

def call(svc,**kw):
    return svc.collect(symbol="RI",exact_secid="RIZ6",timeframe="H1",from_date="2026-10-29",till_date="2026-10-29",**kw)

def test_preview_no_network_then_google_commit_then_repeat_no_source(tmp_path):
    b,m,s=collector(tmp_path)
    assert call(s)["status"]=="ARCHIVE_COLLECTION_PLANNED"
    assert m.calls==0 and b.writes==[]
    r=call(s,dry_run=False,exclusive_workbook_owner=True)
    assert r["canonical_storage"]=="GOOGLE_SHEETS" and r["added_rows"]==1
    assert m.calls==1 and len(b.data["H1"])==2
    r=call(s,dry_run=False,exclusive_workbook_owner=True)
    assert r["status"]=="ARCHIVE_SOURCE_REUSED" and m.calls==1

def test_existing_unproven_window_never_redownloaded(tmp_path):
    b,m,s=collector(tmp_path)
    call(s,dry_run=False,exclusive_workbook_owner=True)
    b.status={};before=deepcopy(b.data)
    r=call(s,dry_run=False,exclusive_workbook_owner=True)
    assert r["status"]=="SOURCE_COVERAGE_UNKNOWN" and b.data==before and m.calls==1

def test_google_edit_invalidates_receipt_without_overwrite(tmp_path):
    b,m,s=collector(tmp_path);call(s,dry_run=False,exclusive_workbook_owner=True)
    b.data["H1"][1][13]=999
    with pytest.raises(MirrorConflict,match="receipt"):
        call(s,dry_run=False,exclusive_workbook_owner=True)
    assert m.calls==1 and b.data["H1"][1][13]==999

def test_wrong_family_and_outside_window_refused(tmp_path):
    b,m,s=collector(tmp_path)
    m.instrument=Instrument(symbol="SBER",secid="SBER",engine="stock",market="shares",board="TQBR",asset_class="equity")
    with pytest.raises(MirrorConflict,match="metadata"):
        call(s,dry_run=False,exclusive_workbook_owner=True)
    assert m.calls==0 and not b.writes

def test_contract_registry_is_data_not_forecast():
    from birzha.application.instrument_archive_registry import archive_contract
    c=archive_contract("RI")
    assert c["canonical_storage"]=="GOOGLE_SHEETS"
    assert c["source_cache_is_not_canonical"] is True
    assert c["forecast_admission"]=="NOT_GRANTED_BY_ARCHIVE"
    assert len(c["indicator_sets"][0]["outputs"])==55
    with pytest.raises(ValueError,match="retired"):archive_contract("RTSI")
