"""Blank-cell planning and fault injection, using only synthetic books."""
from copy import deepcopy
from datetime import datetime,timedelta
import pytest
from birzha.application.model3_google_backfill import plan_ri_field_repair, scope_2026
from birzha.application.model3_features import research_features
from birzha.application.model3_indicator_coverage import project_calculated_rows,COMPACT_RI_INDICATOR_COLUMNS
from birzha.application.model3_data_readiness import readiness_summary
from birzha.application.google_first_archive import GoogleFirstArchive,MSK
from birzha.application.canonical_market_mirror import MirrorConflict
from test_model3_google_backfill import Book,candles

ARGS=dict(timeframe='D1',from_date='2026-01-05',till_date='2026-01-05',
          fields=['p3_range'],max_cells=1)
NOW='2026-11-06T12:00:00+03:00'

def dataset(n=1):
    source=candles();h=source[0];base=dict(zip(h,source[-1]))
    source=source[:-1]
    for i in range(n):
        row=dict(base);day=(datetime(2026,1,5)+timedelta(days=i)).date().isoformat()
        row.update(calendar_date=day,bar_start_time=day+'T00:00:00+03:00',
                   bar_end_time=day+'T23:59:59+03:00',normalized_sha256='sha_'+day)
        row['record_key']='RI|D1|RIH6|'+row['bar_start_time']
        source.append([row[k] for k in h])
    generated=research_features({'D1':source},calculated_at=NOW)['D1']
    ind=[list(COMPACT_RI_INDICATOR_COLUMNS)]+project_calculated_rows(generated,COMPACT_RI_INDICATOR_COLUMNS)
    return source,ind

def plan(raw,ind,**kw):
    opts=dict(ARGS);opts.update(kw)
    return plan_ri_field_repair(candles=raw,daily=raw,existing=ind,calculated_at=NOW,**opts)

def blank(ind,field='p3_range',row=-1):ind[row][ind[0].index(field)]=''

class PatchBook(Book):
    def __init__(self):
        super().__init__();self.source,self.features=dataset();blank(self.features)
        self.fail_after_commit=False;self.before_send=None
    def model3_write_cells(self,s,sheet,edits,**kwargs):
        self.writes+=1
        for r,c,v in edits:self.features[r][c]=v
        if self.fail_after_commit:raise RuntimeError('secret timeout')
def service(b,writer=True):
    s=GoogleFirstArchive(b,None,'a'*40,sole_writer=writer,now=lambda:datetime(2026,11,6,tzinfo=MSK))
    s._schemas=lambda *a:deepcopy(b.source)
    return s
def apply(s,preview=None):
    preview=preview or s.model3_repair_fields(**ARGS)
    return s.model3_repair_fields(**ARGS,dry_run=False,owner_confirmed=True,
        external_writer_exclusion_confirmed=True,expected_plan_sha256=preview['plan_sha256'])

def test_readiness_filters_both_sides_to_2026():
    raw,ind=dataset();r=readiness_summary(raw,ind,timeframe='D1')
    assert r['year']==2026 and r['source_records']==r['indicator_records']==1
    assert r['record_coverage']['missing_indicator_records']==0

def test_only_blank_2026_cell_and_original_coordinates():
    raw,ind=dataset();blank(ind);before=deepcopy(ind)
    p,edits=plan(raw,ind)
    assert len(edits)==1 and edits[0][0]==len(ind)-1 and p['fillable_cells']==1
    assert ind==before and p['gaps'][0]['secid']=='RIH6'
    assert p['gaps'][0]['category']=='MISSING_CALCULATION'

@pytest.mark.parametrize('filled',[0,False,'=A1',123.0])
def test_occupied_values_never_replaced(filled):
    raw,ind=dataset();ind[-1][ind[0].index('p3_range')]=filled
    assert plan(raw,ind)[1]==[]

def test_incomplete_or_missing_hash_blocks_not_zero_filled():
    for field,value in [('is_complete',False),('normalized_sha256','')]:
        raw,ind=dataset();blank(ind);raw[-1][raw[0].index(field)]=value
        p,e=plan(raw,ind);assert not e and p['category_counts']=={'SOURCE_UNRELIABLE':1}

def test_atr_prior_requires_seed_and_extra_previous_bar():
    raw,ind=dataset(16);blank(ind,'p3_prior_atr14')
    p,e=plan(raw,ind,from_date='2026-01-20',till_date='2026-01-20',fields=['p3_prior_atr14'])
    assert len(e)==1 and e[0][2]>0
    raw,ind=dataset(15);blank(ind,'p3_prior_atr14')
    p,e=plan(raw,ind,from_date='2026-01-19',till_date='2026-01-19',fields=['p3_prior_atr14'])
    assert not e and p['category_counts']=={'INSUFFICIENT_WARMUP':1}

def test_rvol_20_prior_same_contract_and_no_future():
    raw,ind=dataset(21);blank(ind,'p3_rvol_same_clock20_approx')
    p,e=plan(raw,ind,from_date='2026-01-25',till_date='2026-01-25',fields=['p3_rvol_same_clock20_approx'])
    assert e[0][2]==1
    raw[-1][raw[0].index('bar_start_time')]='2026-01-26T00:00:00+03:00'
    raw[-1][raw[0].index('record_key')]='RI|D1|RIH6|2026-01-26T00:00:00+03:00'
    ind=ind[:-1];blank(ind,'p3_rvol_same_clock20_approx')
    p,e=plan(raw,ind,from_date='2026-01-24',till_date='2026-01-24',fields=['p3_rvol_same_clock20_approx'])
    assert not e and p['category_counts']=={'INSUFFICIENT_WARMUP':1}

def test_proxy_source_not_manufactured_and_no_exact_fields():
    raw,ind=dataset()
    p,e=plan(raw,ind,fields=['p3_minute_sign_delta_proxy'])
    assert not e and p['gaps'][0]['reason']=='EXISTING_M1_PROXY_SPEC_INTRADAY_ONLY'
    with pytest.raises(MirrorConflict):plan(raw,ind,fields=['true_delta'])

def test_wrong_order_or_wrong_year_rejected():
    raw,ind=dataset();ind[0][3],ind[0][4]=ind[0][4],ind[0][3]
    with pytest.raises(ValueError):plan(raw,ind)
    raw,ind=dataset()
    with pytest.raises(MirrorConflict):plan(raw,ind,from_date='2025-01-05',till_date='2025-01-05')

def test_dry_run_does_not_audit_or_write():
    b=PatchBook();p=service(b,False).model3_repair_fields(**ARGS)
    assert p['planned_cells']==1 and b.audit==[] and b.writes==0

def test_plan_apply_readback_repeat_and_2025_unchanged():
    b=PatchBook();s=service(b);old=deepcopy(b.features);p=s.model3_repair_fields(**ARGS)
    r=apply(s,p);assert r['confirmed_cells']==1 and b.writes==1
    expected=deepcopy(old);expected[-1][old[0].index('p3_range')]=15
    assert b.features==expected and b.features[1:3]==old[1:3]
    assert not apply(service(b))['written'] and b.writes==1

def test_apply_requires_all_explicit_gates():
    b=PatchBook();s=service(b)
    with pytest.raises(MirrorConflict):s.model3_repair_fields(**ARGS,dry_run=False,owner_confirmed=True)
    assert b.writes==0 and b.audit==[]

def test_changed_plan_does_not_write():
    b=PatchBook();s=service(b);p=s.model3_repair_fields(**ARGS)
    b.features[-1][b.features[0].index('p3_body')]=99
    with pytest.raises(MirrorConflict,match='PLAN_CHANGED'):apply(s,p)
    assert b.writes==0

def test_mutation_after_prepared_detected_before_send():
    b=PatchBook();real=b.append_quality
    def mutate(s,e):
        real(s,e)
        if e['status']=='PREPARED':b.features[-1][b.features[0].index('p3_body')]=99
    b.append_quality=mutate
    with pytest.raises(MirrorConflict,match='CONCURRENT_CHANGE'):apply(service(b))
    assert b.writes==0

def test_unknown_response_then_reconcile_never_resubmits():
    b=PatchBook();s=service(b);b.fail_after_commit=True
    with pytest.raises(MirrorConflict,match='UNKNOWN_OUTCOME'):apply(s)
    assert b.writes==1
    assert apply(service(b))['status']=='RECOVERED_COMMITTED' and b.writes==1

def test_unknown_before_commit_stays_pending_and_no_retry():
    b=PatchBook()
    def lose(*args):raise RuntimeError('secret')
    b.model3_write_cells=lose
    with pytest.raises(MirrorConflict,match='UNKNOWN_OUTCOME'):apply(service(b))
    with pytest.raises(MirrorConflict,match='SETTLEMENT_REQUIRED'):apply(service(b))
    assert b.writes==0

def test_unrelated_readback_edit_stops_no_rollback():
    b=PatchBook();real=b.model3_write_cells
    def corrupt(*args,**kwargs):
        real(*args,**kwargs);b.features[1][-1]='external'
    b.model3_write_cells=corrupt
    with pytest.raises(MirrorConflict,match='READBACK_CONFLICT'):apply(service(b))
    assert b.writes==1 and b.features[1][-1]=='external'


def test_future_source_values_cannot_change_earlier_output():
    raw,ind=dataset(2);blank(ind,row=-2)
    a,ea=plan(raw,ind)
    for field in ('open','high','low','close'):
        raw[-1][raw[0].index(field)]+=100000
    b,eb=plan(raw,ind)
    assert ea==eb and len(ea)==1  # signature may conservatively invalidate, value never looks ahead

def test_future_daily_close_is_not_prior_context():
    raw,ind=dataset();future=dict(zip(raw[0],raw[1]))
    future['calendar_date']='2026-01-04'
    future['bar_end_time']='2026-01-10T23:59:59+03:00'
    future['contract_code']='RIH6'
    future['record_key']='RI|D1|RIH6|2026-01-04T00:00:00+03:00'
    future['bar_start_time']='2026-01-04T00:00:00+03:00'
    daily=[raw[0],[future[k] for k in raw[0]]]
    h1=deepcopy(raw);h1=h1[:1]+h1[-1:]
    h1[-1][0]=h1[-1][0].replace('|D1|','|H1|')
    generated=research_features({'D1':daily,'H1':h1},calculated_at=NOW)['H1']
    from birzha.application.model3_features import COLUMNS
    assert generated[0][COLUMNS.index('p3_d1_prior_close')]==''

def test_unknown_volume_not_treated_as_zero():
    raw,ind=dataset(21);blank(ind,'p3_rvol_same_clock20_approx')
    raw[-2][raw[0].index('volume')]=''
    p,e=plan(raw,ind,from_date='2026-01-25',till_date='2026-01-25',fields=['p3_rvol_same_clock20_approx'])
    assert not e and p['gaps'][0]['reason']=='SAVED_VOLUME_MISSING_NOT_ZERO'

@pytest.mark.parametrize('args',[{'max_rows':True},{'max_cells':201},{'fields':['source_record_sha256']}])
def test_limits_and_metadata_fail_closed(args):
    raw,ind=dataset()
    with pytest.raises(MirrorConflict):plan(raw,ind,**args)

def test_audit_failure_prevents_cell_write():
    b=PatchBook()
    b.append_quality=lambda *a:(_ for _ in ()).throw(RuntimeError('secret'))
    with pytest.raises(RuntimeError):apply(service(b))
    assert b.writes==0

def test_status_failure_keeps_committed_patch_without_retry():
    b=PatchBook();b.update_status=lambda *a:(_ for _ in ()).throw(RuntimeError('secret'))
    assert apply(service(b))['status']=='READBACK_PASS_STATUS_UNCONFIRMED'
    assert b.writes==1 and not apply(service(b))['written']

def test_native_existing_only_uses_literal_cells_no_resize():
    from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
    n=NativeCanonicalSheets.__new__(NativeCanonicalSheets)
    n._sheets={('RI','D1_INDICATORS'):{'sheetId':9,'gridProperties':{'rowCount':10,'columnCount':95}}}
    n.validate_model3_target=lambda *a:None
    n._validate_named_target=lambda *a:None
    n.read_rows=lambda *a:[list(COMPACT_RI_INDICATOR_COLUMNS)]+[[""]*95 for _ in range(9)]
    bodies=[]
    n._request=lambda method,url,**kw:bodies.append(kw['json']) or {}
    n.model3_write_cells('RI','D1_INDICATORS',[(1,60,123)],existing_only=True)
    assert len(bodies)==1 and len(bodies[0]['requests'])==1
    assert bodies[0]['requests'][0]['updateCells']['fields']=='userEnteredValue'
    for edit in ([(0,1,1)],[(10,1,1)],[(1,95,1)]):
        with pytest.raises(MirrorConflict,match='EXISTING_GRID_ONLY'):
            n.model3_write_cells('RI','D1_INDICATORS',edit,existing_only=True)
    assert len(bodies)==1

def test_cross_contract_warmup_never_borrowed():
    raw,ind=dataset(20);blank(ind,'p3_rvol_same_clock20_approx')
    # Many observations of RIH5 do not count toward the RIH6 slot seed.
    p,e=plan(raw,ind,from_date='2026-01-24',till_date='2026-01-24',fields=['p3_rvol_same_clock20_approx'])
    assert not e and p['gaps'][0]['category']=='INSUFFICIENT_WARMUP'


def test_atr_reset_is_source_problem_not_initial_warmup():
    raw,ind=dataset(18);blank(ind,'p3_prior_atr14')
    raw[-3][raw[0].index('is_complete')]=False
    p,e=plan(raw,ind,from_date='2026-01-22',till_date='2026-01-22',fields=['p3_prior_atr14'])
    assert not e and p['gaps'][0]['reason']=='ATR_CHAIN_RESET_BY_INCOMPLETE_SOURCE'

def test_previous_dependency_must_be_closed_at_target():
    raw,ind=dataset(2);blank(ind,'p3_prior_high')
    raw[-2][raw[0].index('bar_end_time')]='2026-02-01T00:00:00+03:00'
    p,e=plan(raw,ind,from_date='2026-01-06',till_date='2026-01-06',fields=['p3_prior_high'])
    assert not e and p['gaps'][0]['reason']=='DEPENDENCY_NOT_CLOSED_AT_TARGET'

def test_target_not_closed_at_calculation_is_not_repaired():
    raw,ind=dataset();blank(ind)
    raw[-1][raw[0].index('bar_end_time')]='2027-01-01T00:00:00+03:00'
    p,e=plan(raw,ind)
    assert not e and p['gaps'][0]['reason']=='TARGET_NOT_CLOSED_AT_CALCULATION'


def test_completed_day_boundary_uses_moscow_not_utc():
    from datetime import timezone
    b=PatchBook();s=service(b)
    s.now=lambda:datetime(2026,11,5,22,tzinfo=timezone.utc)  # Nov6 01:00 Moscow
    r=s.model3_repair_fields(timeframe='D1',from_date='2026-11-05',till_date='2026-11-05')
    assert not r['written'] and r['scanned_rows']==0
