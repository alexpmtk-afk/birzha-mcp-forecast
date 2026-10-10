"""No-market-network regression checks for G2 reconstructed directional baseline."""
from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from scripts.g2_first_forecast_development import (
    load_rows, predict, score, run, future_index, future_price_difference,
    FEATURE_VERSION,
)


def row(n, market='SBER', secid='SBER', d5=1.0, d20=2.0, atr=2.0):
    dates=[(date(2021,1,1)+timedelta(days=k)).isoformat() for k in range(n,n+21)]
    return {'market':market,'secid':secid,'session':dates[-1],
      'source':'HISTORICAL_ARCHIVE_RECONSTRUCTED',
      'historical_first_receipt':'NOT_PROVEN',
      'decision_knowledge_cutoff_at':None,'expected_sessions':dates,
      'features':{'schema':FEATURE_VERSION,'secid':secid,'session':dates[-1],
        'eligibility':'RECONSTRUCTED_RESEARCH_ONLY','features':{
          'd5_atr':{'status':'AVAILABLE','value':d5},
          'd20_atr':{'status':'AVAILABLE','value':d20},
          'atr14_sma_tr':{'status':'AVAILABLE','value':atr}}}}


def test_frozen_predictions_read_origin_only():
    origin=row(0,d5=-1)
    ps=predict([origin]);assert len(ps)==3
    assert all(x['prediction']=='DOWN' for x in ps)
    assert all(x['historical_as_known_at_t0'] is False for x in ps)
    assert predict([origin,row(5,d5=900)])[:3]==ps


def test_abstain_on_zero_and_missing():
    assert all(p['prediction']=='ABSTAIN' for p in predict([row(0,d5=0)]))
    r=row(0);r['features']['features']['d5_atr']={'status':'MISSING','value':None}
    assert all(p['prediction']=='ABSTAIN' for p in predict([r]))


def test_exact_five_and_twenty_outcomes():
    rs=[row(0),row(5,d5=-2,atr=3),row(20,d20=4,atr=5)]
    index=future_index(rs)
    origin=rs[0]['session']
    assert future_price_difference(index,'SBER','SBER',origin,5)[0]==-6
    assert future_price_difference(index,'SBER','SBER',origin,20)[0]==20


def test_ten_day_outcome_uses_two_five_day_deltas():
    a,b,c=row(0),row(5,d5=2,atr=3),row(10,d5=-1,atr=5)
    result=future_price_difference(future_index([a,b,c]),'SBER','SBER',a['session'],10)
    assert result[0]==1 and result[1]=='SCORED'


def test_ten_day_missing_midpoint_fails_closed():
    a,c=row(0),row(10)
    assert future_price_difference(future_index([a,c]),'SBER','SBER',a['session'],10)[0] is None


def test_cross_contract_never_stitched():
    a,b=row(0,market='BR',secid='BRH1'),row(5,market='BR',secid='BRJ1')
    result=future_price_difference(future_index([a,b]),'BR','BRH1',a['session'],5)
    assert result[0] is None and 'NO_EXACT_SECID' in result[1]


def test_accuracy_and_baseline_share_scored_rows():
    a,b=row(0,d5=1),row(5,d5=-2)
    ps=predict([a,b]);outs,summary=score(ps,[a,b]);s=summary['SBER:5']
    assert s['candidates']==2 and s['scored']==1
    assert s['correct']==0 and s['always_up_hits_on_same_scored_rows']==0
    assert s['unscorable_reasons']['NO_EXACT_SECID_CONTIGUOUS_FUTURE_WINDOW']==1


def test_reject_wrong_provenance(tmp_path):
    p=tmp_path/'in.jsonl';r=row(0);r['historical_first_receipt']='PROVEN'
    p.write_text(json.dumps(r)+'\n',encoding='utf8')
    with pytest.raises(AssertionError,match='false_first_receipt'):load_rows(p)


def test_reject_duplicate(tmp_path):
    p=tmp_path/'in.jsonl';r=row(0)
    p.write_text(json.dumps(r)+'\n'+json.dumps(r)+'\n',encoding='utf8')
    with pytest.raises(AssertionError,match='duplicate'):load_rows(p)


def test_determinism(tmp_path):
    p=tmp_path/'in.jsonl'
    p.write_text('\n'.join(json.dumps(r) for r in [row(0),row(5)])+'\n',encoding='utf8')
    a,b=run(p,tmp_path/'a'),run(p,tmp_path/'b')
    assert a==b
    assert (tmp_path/'a/g2_first_forecast_predictions.jsonl').read_bytes()==(tmp_path/'b/g2_first_forecast_predictions.jsonl').read_bytes()
