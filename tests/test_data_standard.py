from copy import deepcopy
import pytest
from birzha.application.data_standard import CANDLE_COLUMNS, migrate_research_row, semantic_metadata
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.application.home_market_mirror import project_source
from test_home_market_mirror import store

@pytest.mark.parametrize('tf',['D1','H1','M15'])
def test_standard_lossless_and_existing_mirror_compatible(tf):
    db=store(); bundle=db.export_mirror_bundle('SBER','2026-10-12','2026-10-12')
    old,_=project_source(bundle,tf,[])
    new=[CANDLE_COLUMNS]+[migrate_research_row(old[0],row,'SBER',tf) for row in old[1:]]
    assert len(CANDLE_COLUMNS)==40 and len(set(CANDLE_COLUMNS))==40
    for key in old[0]: assert new[1][CANDLE_COLUMNS.index(key)]==old[1][old[0].index(key)]
    result,evidence=project_source(bundle,tf,new)
    assert result==new and evidence['matched_source_bars']==1
    empty,_=project_source(bundle,tf,[CANDLE_COLUMNS])
    assert empty[1][CANDLE_COLUMNS.index('secid')]=='SBER'
    assert empty[1][CANDLE_COLUMNS.index('first_seen_at')]==''
    db.close()

def test_index_volume_is_not_activity_and_receipt_is_not_imputed():
    raw={'secid':'RTSI','volume':0,'value':None,'available_at':'2020-01-01'}
    meta=semantic_metadata('RTSI','D1',raw)
    assert meta['volume_status']=='NOT_APPLICABLE_INDEX'
    assert meta['observed_at']==meta['first_seen_at']=='' and raw['volume']==0

def test_unknown_columns_and_mismatched_identity_fail_closed():
    db=store();bundle=db.export_mirror_bundle('SBER','2026-10-12','2026-10-12')
    old,_=project_source(bundle,'D1',[])
    with pytest.raises(ValueError): migrate_research_row(old[0]+['unmapped'],old[1],'SBER','D1')
    row=migrate_research_row(old[0],old[1],'SBER','D1');row[CANDLE_COLUMNS.index('secid')]='OTHER'
    with pytest.raises(ValueError):migrate_research_row(CANDLE_COLUMNS,row,'SBER','D1')
    db.close()

@pytest.mark.parametrize('column,value',[('secid','OTHER'),('timeframe','M15')])
def test_mirror_rejects_conflicting_extended_identity(column,value):
    db=store();bundle=db.export_mirror_bundle('SBER','2026-10-12','2026-10-12')
    old,_=project_source(bundle,'D1',[])
    row=migrate_research_row(old[0],old[1],'SBER','D1');row[CANDLE_COLUMNS.index(column)]=value
    with pytest.raises(MirrorConflict):project_source(bundle,'D1',[CANDLE_COLUMNS,row])
    db.close()
