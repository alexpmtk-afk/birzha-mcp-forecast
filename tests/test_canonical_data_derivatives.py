from copy import deepcopy
from datetime import datetime, timedelta
import json
import httpx
import pytest
from birzha.application.canonical_data_derivatives import (
    HEADERS, INDICATOR_COLUMNS, FLOW_COLUMNS, POSITION_COLUMNS, indicator_rows,
    flow_rows, plan_rows, publish, derived_rectangle,
)
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.application.home_market_mirror import HomeCanonicalMirror, project_source
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore
from test_home_market_mirror import Book, store


def candles(symbol='SBER',count=55):
    db=store();bundle=db.export_mirror_bundle('SBER','2026-10-12','2026-10-12')
    base,_=project_source(bundle,'D1',[]);db.close();header=base[0];rows=[]
    for i in range(count):
        raw=dict(zip(header,base[1]));day=(datetime(2026,1,1)+timedelta(days=i)).date().isoformat()
        raw.update(record_key=f'{symbol}|D1|{symbol}|{day}T00:00:00',contract_code=symbol,calendar_date=day,bar_start_time=day+'T00:00:00',bar_end_time=day+'T23:59:59',open=100+i,high=102+i,low=99+i,close=101+i)
        rows.append([raw[c] for c in header])
    return {'D1':[header,*rows]}


class DerivedBook(Book):
    def __init__(self):
        super().__init__();self.data.update({name:[columns[:]] for name,columns in HEADERS.items()})
        self.lose_derived_response=False
    def validate_derived_target(self,symbol,sheet):
        if sheet not in self.data: raise MirrorConflict('missing derived tab')
    def append_rows(self,symbol,sheet,rows):
        super().append_rows(symbol,sheet,rows)
        if sheet in HEADERS and self.lose_derived_response:
            self.lose_derived_response=False
            raise MirrorConflict('lost response after committed derived append')


def test_idempotent_indicator_plan_preserves_original_calculation_clock_and_v1():
    rows,version=indicator_rows('SBER',candles(),calculated_at='2026-10-18T00:00:00Z')
    old=[INDICATOR_COLUMNS,*rows]
    later,_=indicator_rows('SBER',candles(),calculated_at='2026-10-19T00:00:00Z')
    assert plan_rows('INDICATORS',old,later)[1]==[]
    prior=deepcopy(rows[0]);prior[0]='frozen_v1';prior[12]='old_formula'
    assert plan_rows('INDICATORS',[INDICATOR_COLUMNS,prior],rows)[0][1]==derived_rectangle([INDICATOR_COLUMNS,prior])[1]
    assert 'REVISION_WINDOWS_V2' in version


def test_source_revision_changes_identity_without_overwriting_old_calculation():
    source=candles();first,_=indicator_rows('SBER',source,calculated_at='fixed')
    source['D1'][20][source['D1'][0].index('close')]+=0.5
    second,_=indicator_rows('SBER',source,calculated_at='fixed')
    assert first[:19]==second[:19]
    assert first[19][0]!=second[19][0]
    assert plan_rows('INDICATORS',[INDICATOR_COLUMNS,*first],second)[1]


def test_future_rows_cannot_change_existing_derived_prefix():
    before,_=indicator_rows('SBER',candles(count=35),calculated_at='fixed')
    after,_=indicator_rows('SBER',candles(count=55),calculated_at='fixed')
    assert before==after[:35]


def test_incomplete_bar_resets_window_and_does_not_impute_unknown_volume():
    source=candles();header=source['D1'][0]
    source['D1'][30][header.index('is_complete')]=False
    source['D1'][52][header.index('volume')]=''
    rows,_=indicator_rows('SBER',source)
    assert rows[29][INDICATOR_COLUMNS.index('source_window_count')]==0
    assert rows[30][INDICATOR_COLUMNS.index('source_window_count')]==1
    last=dict(zip(INDICATOR_COLUMNS,rows[-1]))
    assert last['volume_ratio_20'] is None
    assert json.loads(last['indicator_status'])['volume_ratio_20']=='MISSING_VOLUME_IN_WINDOW'


@pytest.mark.parametrize('symbol',['IMOEX','RTSI'])
def test_index_raw_zero_not_promoted_to_volume_feature(symbol):
    rows,_=indicator_rows(symbol,candles(symbol))
    last=dict(zip(INDICATOR_COLUMNS,rows[-1]))
    assert last['volume_ratio_20'] is None and last['vwap_20_candle_approx'] is None
    assert last['sma_20'] is not None


def test_aware_and_provider_clock_aliases_are_detected():
    source=candles(count=2);header=source['D1'][0]
    second=source['D1'][2];second[header.index('bar_start_time')]='2025-12-31T21:00:00+00:00'
    second[header.index('record_key')]='SBER|D1|SBER|2025-12-31T21:00:00+00:00'
    with pytest.raises(MirrorConflict,match='duplicate semantic'): indicator_rows('SBER',source)


def flow_source(dataset='FUTOI',key='GOLD',payload=None):
    return dict(dataset=dataset,key_symbol=key,row_key='source-key',payload=payload or dict(ticker='GD',tradedate='2026-10-12',tradetime='12:00:00',pos_long=20,pos_short=-15,pos=5),available_at=None,available_at_confidence='UNKNOWN',observed_at='2026-10-13T00:00:00Z')


def test_futoi_gd_underlying_is_not_contract_oi_or_fabricated_receipt():
    result=flow_rows('GOLD',[flow_source()]);r=dict(zip(POSITION_COLUMNS,result['POSITIONS'][0]))
    assert r['underlying']=='GD' and r['secid'] is None and r['oi'] is None
    assert r['scope_type']=='UNDERLYING_ALL_CONTRACTS' and r['available_at'] is None
    assert r['pos_short']==-15 and 'payload' not in r


def test_wrong_futoi_ticker_and_wrong_exact_contract_are_rejected():
    wrong=flow_source();wrong['payload']['ticker']='Si'
    with pytest.raises(MirrorConflict):flow_rows('GOLD',[wrong])
    with pytest.raises(MirrorConflict):flow_rows('GOLD',[flow_source('TRADESTATS','GOLD')])


def test_partial_delta_does_not_invent_unknown_volume_or_session_cd():
    raw=dict(tradedate='2026-10-12',tradetime='12:00:00',vol_b=10,vol_s=4)
    r=dict(zip(FLOW_COLUMNS,flow_rows('SI',[flow_source('TRADESTATS','SiZ6',raw)])['FLOW'][0]))
    assert r['delta']==6 and r['delta_ratio']==6/14
    assert r['unknown_volume'] is None and r['cumulative_delta'] is None
    assert 'CLASSIFIED_VOLUME' in r['methodology'] and r['first_seen_at'] is None


@pytest.mark.parametrize('symbol,key,expected',[('SBER','SBER',1),('SI','SiZ6',1),('BR','BRX6',1),('GOLD','GDZ6',1),('IMOEX','IMOEX',0),('RTSI','RTSI',0)])
def test_flow_export_scope_bounded_read_only_existing_connection(symbol,key,expected):
    db=DuckDBHistoricalFlowStore()
    raw=dict(tradedate='2026-10-12',tradetime='12:00:00',vol_b=10,vol_s=4)
    db.upsert_rows('TRADESTATS',key,[raw],'test')
    db.upsert_rows('TRADESTATS','GOLD',[raw],'wrong-equity')
    before=db._connection.execute('SELECT * FROM historical_flow_rows ORDER BY key_symbol').fetchall()
    rows=db.export_mirror_bundle(symbol,'2026-10-12','2026-10-12')
    assert len(rows)==expected
    assert db._connection.execute('SELECT * FROM historical_flow_rows ORDER BY key_symbol').fetchall()==before
    with pytest.raises(ValueError):db.export_mirror_bundle(symbol,'2020-01-01','2026-10-12')
    db.close()


def test_missing_last_derived_tab_blocks_every_write():
    db=store();flow=DuckDBHistoricalFlowStore();book=DerivedBook();del book.data['POSITIONS']
    service=HomeCanonicalMirror(db,book,'a'*40,flow_source=flow)
    with pytest.raises(MirrorConflict):service.sync(symbol='SBER',from_date='2026-10-12',till_date='2026-10-12',dry_run=False,exclusive_workbook_owner=True,include_derived=True)
    assert book.writes==[]
    db.close();flow.close()


def test_whole_writer_dry_repeat_and_lost_derived_response_resume():
    db=store();flow=DuckDBHistoricalFlowStore();book=DerivedBook()
    service=HomeCanonicalMirror(db,book,'a'*40,flow_source=flow)
    args=dict(symbol='SBER',from_date='2026-10-12',till_date='2026-10-12',include_derived=True)
    dry=service.sync(**args)
    assert dry['derived_planned_additions']=={'INDICATORS':3,'FLOW':0,'POSITIONS':0}
    assert book.writes==[]
    book.lose_derived_response=True
    with pytest.raises(MirrorConflict):service.sync(**args,dry_run=False,exclusive_workbook_owner=True)
    assert len(book.data['INDICATORS'])==4
    assert dict(book.data['SYNC_STATUS'][1:])['derived_v2_status']=='FAILED_RECONCILE_BEFORE_RETRY'
    result=service.sync(**args,dry_run=False,exclusive_workbook_owner=True)
    assert all(x['added_rows']==0 for x in result['derived'].values())
    assert len(book.data['INDICATORS'])==4
    final=service.sync(**args,dry_run=False,exclusive_workbook_owner=True)
    assert all(x['added_rows']==0 for x in final['derived'].values())
    db.close();flow.close()


def test_changed_destination_or_payload_conflict_refused():
    row=indicator_rows('SBER',candles(count=1))[0][0]
    old=[INDICATOR_COLUMNS,row];bad=deepcopy(row);bad[20]=1
    with pytest.raises(MirrorConflict):plan_rows('INDICATORS',old,[bad])
    book=DerivedBook()
    with pytest.raises(MirrorConflict):publish(book,'SBER',{'INDICATORS':(old,[])})
    assert book.writes==[]

@pytest.mark.parametrize('sheet', ['INDICATORS','FLOW','POSITIONS'])
def test_native_explicit_derived_target_refresh_and_public_validator_stays_strict(sheet):
    from test_native_service_sheets import api
    transport,state=api(derived=True)
    state['rows'][sheet]=[HEADERS[sheet]]
    transport.validate_derived_target('SBER',sheet)
    row=['test-key']+['']*(len(HEADERS[sheet])-1)
    transport.append_rows('SBER',sheet,[row])
    assert derived_rectangle(transport.read_rows('SBER',sheet))==[HEADERS[sheet],row]
    with pytest.raises(MirrorConflict):transport.validate_target('SBER',sheet)
    with pytest.raises(MirrorConflict):transport.validate_service_target('SBER',sheet)


def test_complete_optional_route_through_native_transport_not_only_book_stub():
    from test_native_service_sheets import api
    transport,state=api(derived=True)
    for name,header in HEADERS.items():state['rows'][name]=[header]
    db=store();flow=DuckDBHistoricalFlowStore()
    flow.upsert_rows('TRADESTATS','SBER',[dict(tradedate='2026-10-12',tradetime='12:00:00',vol_b=10,vol_s=4)],'PUBLIC_TRADES_DERIVED')
    before=flow._connection.execute('SELECT * FROM historical_flow_rows').fetchall()
    service=HomeCanonicalMirror(db,transport,'a'*40,flow_source=flow)
    args=dict(symbol='SBER',from_date='2026-10-12',till_date='2026-10-12',include_derived=True)
    assert service.sync(**args)['derived_planned_additions']=={'INDICATORS':3,'FLOW':1,'POSITIONS':0}
    assert not state['posts']
    first=service.sync(**args,dry_run=False,exclusive_workbook_owner=True)
    assert first['derived']['FLOW']['added_rows']==1
    assert len(state['rows']['INDICATORS'])==4
    second=service.sync(**args,dry_run=False,exclusive_workbook_owner=True)
    assert all(v['added_rows']==0 for v in second['derived'].values())
    assert flow._connection.execute('SELECT * FROM historical_flow_rows').fetchall()==before
    db.close();flow.close()


def test_derived_final_cross_sheet_check_catches_later_change():
    book=DerivedBook()
    row=indicator_rows('SBER',candles(count=1))[0][0]
    old=deepcopy(book.data['INDICATORS'])
    original=book.validate_derived_target
    def mutate(symbol,sheet):
        original(symbol,sheet)
        if sheet=='FLOW' and len(book.data['INDICATORS'])>1:
            book.data['INDICATORS'][1][20]=9
    book.validate_derived_target=mutate
    plans={'INDICATORS':(old,derived_rectangle([INDICATOR_COLUMNS,row])[1:]),'FLOW':(book.data['FLOW'],[])}
    with pytest.raises(MirrorConflict,match='cross-derived'):publish(book,'SBER',plans)


def test_transfer_batches_remain_under_native_payload_limit():
    from birzha.application.canonical_data_derivatives import append_batches
    rows=[['key'+str(i),'Ж'*4000] for i in range(500)]
    chunks=append_batches(rows)
    assert sum(map(len,chunks))==500 and len(chunks)>1
    for chunk in chunks:
        body={'requests':[{'appendCells':{'sheetId':123,'fields':'userEnteredValue','rows':[{'values':[{'userEnteredValue':{'stringValue':v}} for v in row]} for row in chunk]}}]}
        assert len(json.dumps(body,ensure_ascii=False).encode())<1000000


def test_duplicate_keys_and_oversized_derived_rows_fail_before_any_publication():
    rows,_=indicator_rows('SBER',candles(count=1))
    with pytest.raises(MirrorConflict):plan_rows('INDICATORS',[INDICATOR_COLUMNS,*rows,*rows],rows)
    oversized=deepcopy(rows[0]);oversized[19]='x'*20000
    with pytest.raises(MirrorConflict,match='transfer size'):plan_rows('INDICATORS',[INDICATOR_COLUMNS],[oversized])
