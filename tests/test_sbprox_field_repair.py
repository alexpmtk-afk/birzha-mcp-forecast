"""Synthetic-only acceptance of shared SBProX cell writer and receipt recovery."""
from copy import deepcopy
from datetime import datetime
import threading
from types import SimpleNamespace
from contextlib import nullcontext
import pytest
from test_sbprox_integration import source, reference, NOW
from birzha.application.sbprox_field_repair import repair, SCHEMA_SHA256
from birzha.application.model3_indicator_coverage import RI_SBPROX_INDICATOR_COLUMNS as HEADER
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.storage.google_archive_transport import AUDIT

ARGS=dict(exact_secid='RIH6',timeframe='M15',start='2026-02-11T09:00:00+03:00',end='2026-02-11T09:15:00+03:00')

class Book:
    def __init__(self,source):
        self.source=source;self.audit=[];self.writes=0;self.status={}
        self.raw=[['record_key','contract_code','bar_start_time','open','high','low','close','volume','is_complete','normalized_sha256','bar_end_time'],
                  ['key','RIH6',ARGS['start'],100,100,100,100,150,True,'raw-sha',ARGS['end']]]
        self.rows=[list(HEADER),['']*107,['']*107]
        for k,v in dict(symbol='RI',secid='RIH6',timeframe='M15',begin=ARGS['start'],
                        record_key='indicator-key',source_record_key='key',source_record_sha256='raw-sha',source_complete_flag=True).items():
            self.rows[2][HEADER.index(k)]=v
        # A prior historical row and a legacy formula must survive untouched.
        self.rows[1][HEADER.index('begin')]='2025-02-11T09:00:00+03:00'
        self.rows[1][HEADER.index('secid')]='RIH5'
        self.rows[2][HEADER.index('p3_body')]='=1+2'
    def validate_model3_target(self,*a):pass
    def validate_service_target(self,*a):pass
    def read_rows(self,*a):return deepcopy(self.rows)
    def read_sbprox_master(self):return dict(self.source)
    def writer_guard(self,*a):return nullcontext()
    def read_service_rows(self,*a):return [AUDIT]+[[x.get(k,'') for k in AUDIT] for x in self.audit]
    def append_quality(self,s,e):self.audit.append(deepcopy(e))
    def update_status(self,s,x):self.status.update(x)
    def model3_write_cells(self,s,sheet,edits,**kw):
        assert kw==dict(existing_only=True,sbprox_only=True)
        self.writes+=1
        for r,c,v in edits:self.rows[r][c]=v


def service(b,writer=True):
    return SimpleNamespace(transport=b,sole_writer=writer,source_sha='a'*40,now=lambda:NOW,
                           lock=threading.Lock(),_schemas=lambda *a:deepcopy(b.raw))


def apply(s,plan=None,**overrides):
    plan=plan or repair(s,**ARGS)
    kw=dict(ARGS,dry_run=False,owner_confirmed=True,external_writer_exclusion_confirmed=True,
            approved_schema_sha256=SCHEMA_SHA256,expected_plan_sha256=plan['plan_sha256'])
    kw.update(overrides)
    return repair(s,**kw)


def test_preview_apply_readback_repeat_preserves_all_old_cells(source):
    b=Book(source);s=service(b);old=deepcopy(b.rows)
    p=repair(s,**ARGS);assert p['planned_cells']==12 and not b.audit and not b.writes
    assert apply(s,p)['confirmed_cells']==12
    assert [r[:95] for r in b.rows]==[r[:95] for r in old] and b.rows[1]==old[1]
    assert [e['status'] for e in b.audit]==['STARTED','PREPARED','COMMITTED']
    assert not apply(service(b))['written'] and b.writes==1


@pytest.mark.parametrize('gate', ['sole_writer','owner_confirmed','external_writer_exclusion_confirmed','approved_schema_sha256','expected_plan_sha256'])
def test_explicit_gates_before_any_mutation(source,gate):
    b=Book(source);s=service(b);kw={gate:False if gate.endswith('confirmed') else ''}
    if gate=='sole_writer':s.sole_writer=False;kw={}
    with pytest.raises(MirrorConflict,match='REQUIRED'):apply(s,**kw)
    assert not b.audit and not b.writes


def test_single_cell_pilot_is_bounded_and_next_plan_preserves_it(source):
    b=Book(source);s=service(b);p=repair(s,**ARGS,max_cells=1)
    assert apply(s,p,max_cells=1)['confirmed_cells']==1
    assert b.rows[2][95]==150
    assert repair(s,**ARGS)['planned_cells']==11


@pytest.mark.parametrize('change',['indicators','candles','zip'])
def test_change_after_prepared_stops_before_send(source,change):
    b=Book(source);original=b.append_quality
    def mutate(s,e):
        original(s,e)
        if e['status']=='PREPARED':
            if change=='indicators':b.rows[2][95]='external'
            elif change=='candles':b.raw[1][3]=101
            else:b.source=dict(b.source,sha256='0'*64)
    b.append_quality=mutate
    with pytest.raises(MirrorConflict,match='CONCURRENT_CHANGE'):apply(service(b))
    assert b.writes==0


@pytest.mark.parametrize('committed',[True,False])
def test_lost_response_recovery_does_not_resubmit(source,committed):
    b=Book(source);real=b.model3_write_cells
    def lose(*a,**kw):
        if committed:real(*a,**kw)
        raise RuntimeError('secret must not enter receipts')
    b.model3_write_cells=lose;s=service(b)
    with pytest.raises(MirrorConflict,match='UNKNOWN_OUTCOME'):apply(s)
    if committed:
        r=apply(service(b));assert r['recovery_only'] and r['confirmed_cells']==12
    else:
        with pytest.raises(MirrorConflict,match='SETTLEMENT_REQUIRED'):apply(service(b))
    assert b.writes==int(committed) and 'secret' not in str(b.audit)


def test_schema_missing_and_filled_conflict_block(source):
    b=Book(source);b.rows=[r[:95] for r in b.rows];s=service(b)
    assert repair(s,**ARGS)['planned_cells']==0
    with pytest.raises(MirrorConflict,match='COLUMNS_REQUIRED'):apply(s)
    b=Book(source);b.rows[2][95]=999
    p=repair(service(b),**ARGS)
    assert p['planned_cells']==0 and 'PROVENANCE_CONFLICT' in str(p)


def test_pending_receipt_conflict_and_readback_foreign_change(source):
    b=Book(source);real=b.model3_write_cells
    def other(*a,**kw):
        real(*a,**kw);b.rows[1][10]='external'
    b.model3_write_cells=other
    with pytest.raises(MirrorConflict,match='READBACK_CONFLICT'):apply(service(b))
    with pytest.raises(MirrorConflict,match='RECOVERY_CONFLICT'):apply(service(b))
    assert b.writes==1 and b.rows[1][10]=='external'


def test_stale_plan_and_invalid_bounds(source):
    b=Book(source);s=service(b);p=repair(s,**ARGS);b.rows[1][10]='change'
    with pytest.raises(MirrorConflict,match='PLAN_CHANGED'):apply(s,p)
    for n in [0,201,True]:
        with pytest.raises(MirrorConflict,match='MAX_CELLS'):repair(s,**ARGS,max_cells=n)
    assert not b.writes


def test_audit_failure_and_process_lock_prevent_send(source):
    b=Book(source);s=service(b);s.lock.acquire()
    with pytest.raises(MirrorConflict,match='BUSY'):apply(s)
    assert not b.audit
    s.lock.release()
    b.append_quality=lambda *a:(_ for _ in ()).throw(RuntimeError('audit failed'))
    with pytest.raises(RuntimeError):apply(s)
    assert not b.writes


def test_old_planners_preserve_extension_and_leave_new_rows_blank():
    from test_model3_field_repair import dataset,blank,plan
    from birzha.application.model3_google_backfill import plan_ri_indicator_backfill
    from birzha.application.model3_data_readiness import readiness_summary
    raw,ind=dataset();ind[0]=list(HEADER)
    for r in ind[1:]:r.extend(['saved']*12)
    blank(ind);p,edits=plan(raw,ind)
    assert len(edits)==1 and edits[0][1]<95
    assert readiness_summary(raw,ind,timeframe='D1')['year']==2026
    p,rows=plan_ri_indicator_backfill(candles=raw,daily=raw,existing=ind[:1]+ind[1:-1],timeframe='D1',
        from_date='2026-01-05',till_date='2026-01-05',max_rows=1,calculated_at=NOW.isoformat())
    assert len(rows[0])==107 and rows[0][95:]==['']*12


def test_native_google_patch_is_only_one_blank_extension_cell(source):
    import json
    import httpx
    from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
    from birzha.storage.google_sheets_bridge import INSTRUMENT_FOLDER_IDS,INSTRUMENT_TARGETS
    b=Book(source);target=INSTRUMENT_TARGETS['RI'];sent=[]
    def handler(req):
        if 'drive/v3/files' in str(req.url):
            return httpx.Response(200,json={'id':target.spreadsheet_id,'name':target.spreadsheet_name,
                'mimeType':'application/vnd.google-apps.spreadsheet','trashed':False,'parents':[INSTRUMENT_FOLDER_IDS['RI']]})
        if req.method=='POST':
            sent.append(json.loads(req.content));return httpx.Response(200,json={})
        if '/values/' in str(req.url):return httpx.Response(200,json={'values':b.rows})
        return httpx.Response(200,json={'spreadsheetId':target.spreadsheet_id,'sheets':[{'properties':{
            'sheetId':99,'title':'M15_INDICATORS','sheetType':'GRID','gridProperties':{'rowCount':3,'columnCount':107}}}]})
    t=NativeCanonicalSheets(lambda:'test',transport=httpx.MockTransport(handler))
    t.model3_write_cells('RI','M15_INDICATORS',[(2,95,150)],existing_only=True,sbprox_only=True)
    assert sent==[{'requests':[{'updateCells':{'start':{'sheetId':99,'rowIndex':2,'columnIndex':95},
        'rows':[{'values':[{'userEnteredValue':{'numberValue':150}}]}],'fields':'userEnteredValue'}}]}]
    for edits in [[(2,94,1)],[(0,95,1)],[(3,95,1)],[(2,107,1)],[(2,95,1),(2,95,2)]]:
        with pytest.raises(MirrorConflict):t.model3_write_cells('RI','M15_INDICATORS',edits,existing_only=True,sbprox_only=True)
    b.rows[2][95]='=1+1'
    with pytest.raises(MirrorConflict,match='BLANK_CELL'):t.model3_write_cells('RI','M15_INDICATORS',[(2,95,1)],existing_only=True,sbprox_only=True)
    assert len(sent)==1


def test_actual_mcp_repair_preview_and_explicit_pilot(source):
    import asyncio,json,httpx
    from birzha.mcp.archive_server import build_app
    b=Book(source);app=build_app(service(b),'test')
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
                h={'Authorization':'Bearer test','Accept':'application/json, text/event-stream'}
                r=await c.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{
                    'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
                if r.headers.get('mcp-session-id'):h['mcp-session-id']=r.headers['mcp-session-id']
                h['MCP-Protocol-Version']=r.json()['result']['protocolVersion']
                await c.post('/mcp',headers=h,json={'jsonrpc':'2.0','method':'notifications/initialized'})
                async def call(args):
                    r=await c.post('/mcp',headers=h,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'archive.sbprox_repair_fields','arguments':args}})
                    v=r.json()['result'];return v.get('structuredContent') or json.loads(v['content'][0]['text'])
                p=await call(dict(ARGS,max_cells=1));assert not b.writes and not b.audit
                assert p['planned_cells']==1
                r=await call(dict(ARGS,max_cells=1,dry_run=False,owner_confirmed=True,
                    external_writer_exclusion_confirmed=True,approved_schema_sha256=SCHEMA_SHA256,expected_plan_sha256=p['plan_sha256']))
                assert r['written'] and r['confirmed_cells']==1 and b.writes==1
    asyncio.run(run())


def test_shared_receipt_blocks_old_model3_writer_after_unknown_sbprox_result(source):
    from birzha.application.model3_write_audit import recover
    b=Book(source)
    b.model3_write_cells=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('timeout'))
    with pytest.raises(MirrorConflict):apply(service(b))
    with pytest.raises(MirrorConflict,match='SETTLEMENT_REQUIRED'):recover(service(b))
    assert not b.writes


def test_unverified_day_and_source_identity_do_not_plan_cells(source):
    b=Book(source);b.raw[1][-1]='2026-02-12T00:00:00+03:00'
    assert not repair(service(b),**ARGS)['planned_cells']
    b=Book(source);b.rows[2][HEADER.index('source_record_sha256')]='other'
    assert not repair(service(b),**ARGS)['planned_cells']
