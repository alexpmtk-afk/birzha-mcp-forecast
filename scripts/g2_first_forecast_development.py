"""Frozen, zero-fit directional baseline on G2 reconstructed Development D1 rows.

This script NEVER claims historical ex-ante forecasting. Source rows have no proven
historical first-receipt/vintage. The only label reader uses future feature records,
strictly after prediction rows have been frozen in memory and written to disk.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

FEATURE_VERSION = 'G2_D1_RESEARCH_SMA_TR14_D20_ER20_W20_V1'
EXPERIMENT_VERSION = 'G2_FIRST_FORECAST_D5_SIGN_ZERO_FIT_V1'
MARKETS = ('SBER','Si','BR','GOLD','IMOEX','RTSI')
HORIZONS = (5,10,20)


def stable_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows(path: Path):
    rows=[]
    seen=set()
    for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        r=json.loads(line)
        m=r['market']; session=r['session']; secid=r['secid']
        assert m in MARKETS, ('unsupported_market',n,m)
        assert '2021-01-01' <= session <= '2022-12-31', ('out_of_development',n,session)
        assert r['source']=='HISTORICAL_ARCHIVE_RECONSTRUCTED', ('bad_source',n)
        assert r['decision_knowledge_cutoff_at'] is None, ('false_historical_cutoff',n)
        assert r['historical_first_receipt']=='NOT_PROVEN', ('false_first_receipt',n)
        assert r['features']['schema']==FEATURE_VERSION, ('feature_version_mismatch',n)
        assert r['features']['secid']==secid and r['features']['session']==session
        exp=r['expected_sessions']
        assert len(exp)==21 and exp[-1]==session and len(set(exp))==21 and exp==sorted(exp), ('not_21_sorted_sessions',n)
        assert (m,session) not in seen, ('duplicate',m,session)
        seen.add((m,session))
        assert r['features']['eligibility']=='RECONSTRUCTED_RESEARCH_ONLY'
        rows.append(r)
    assert rows, 'empty_source'
    return sorted(rows,key=lambda r:(MARKETS.index(r['market']),r['session']))


def feature(row,name):
    v=row['features']['features'][name]
    if v['status']!='AVAILABLE' or v['value'] is None or not isinstance(v['value'],(float,int)):
        return None
    value=float(v['value'])
    if not math.isfinite(value):return None
    return value


def classify_direction(value):
    if value>0: return 'UP'
    if value<0: return 'DOWN'
    return 'FLAT'


def predict(rows):
    """No future records or labels are accessible to this function."""
    predictions=[]
    for r in rows:
        d5=feature(r,'d5_atr')
        sign=classify_direction(d5) if d5 is not None else 'ABSTAIN'
        if sign=='FLAT':sign='ABSTAIN'
        for h in HORIZONS:
            predictions.append({
                'experiment_version':EXPERIMENT_VERSION,
                'feature_version':FEATURE_VERSION,
                'market':r['market'], 'secid':r['secid'], 'origin_session':r['session'],
                'horizon_sessions':h,'prediction':sign,
                'prediction_reason':'D5_MOMENTUM_SIGN_ZERO_FIT' if sign!='ABSTAIN' else 'D5_MISSING_OR_ZERO',
                'comparator_prediction':'UP',
                'research_only':True,'historical_as_known_at_t0':False,
            })
    return predictions


def future_index(rows):
    """Map source session to future rows using authenticated 21-session windows.

    A future record can be matched only to the same exact contract SECID.
    No row-index, calendar-day or cross-roll shortcuts.
    """
    out={}
    for r in rows:
        for h in HORIZONS:
            k=(r['market'],r['secid'],r['expected_sessions'][-h-1],h)
            assert k not in out, ('ambiguous_calendar_evidence',k)
            out[k]=r
    return out


def future_price_difference(index,market,secid,origin,h):
    """Reconstruct close(t+h)-close(t) from *future* d5/d20 and ATR.

    This is outcome-only: it must never enter predict().
    """
    fut=index.get((market,secid,origin,h))
    if fut is None:
        return None,'NO_EXACT_SECID_CONTIGUOUS_FUTURE_WINDOW',None
    if h in (5,20):
        d=feature(fut,'d5_atr' if h==5 else 'd20_atr')
        atr=feature(fut,'atr14_sma_tr')
        if d is None or atr is None or atr<=0:
            return None,'MISSING_FUTURE_OUTCOME_FEATURE',fut['session']
        return d*atr,'SCORED',fut['session']
    assert h==10
    mid=index.get((market,secid,origin,5))
    if mid is None or fut['expected_sessions'][-6]!=mid['session']:
        return None,'NO_EXACT_SECID_CONTIGUOUS_MIDPOINT',fut['session']
    d1, a1=feature(mid,'d5_atr'),feature(mid,'atr14_sma_tr')
    d2, a2=feature(fut,'d5_atr'),feature(fut,'atr14_sma_tr')
    if any(x is None for x in (d1,a1,d2,a2)) or a1<=0 or a2<=0:
        return None,'MISSING_FUTURE_OUTCOME_FEATURE',fut['session']
    return d1*a1+d2*a2,'SCORED',fut['session']


def score(predictions,rows):
    idx=future_index(rows)
    outcomes=[]
    summary={}
    for p in predictions:
        delta, status, target_session=future_price_difference(idx,p['market'],p['secid'],p['origin_session'],p['horizon_sessions'])
        actual=classify_direction(delta) if delta is not None else None
        outcomes.append({'market':p['market'],'secid':p['secid'],'origin_session':p['origin_session'],
          'horizon_sessions':p['horizon_sessions'],'target_session':target_session,
          'outcome_status':status,'outcome_direction':actual,
          'realized_close_difference_reconstructed':delta,
          'prediction':p['prediction'],'comparator_prediction':p['comparator_prediction'],
          'correct':int(p['prediction']==actual) if actual is not None and p['prediction']!='ABSTAIN' else None,
          'comparator_correct':int(p['comparator_prediction']==actual) if actual is not None and p['prediction']!='ABSTAIN' else None,
          'research_only':True,'historical_as_known_at_t0':False})
    for market in MARKETS:
      for h in HORIZONS:
        sample=[o for o in outcomes if o['market']==market and o['horizon_sessions']==h]
        issued=sum(o['prediction']!='ABSTAIN' for o in sample)
        valid=[o for o in sample if o['correct'] is not None]
        baseline=sum(o['comparator_correct'] for o in valid)
        hits=sum(o['correct'] for o in valid)
        flat=sum(o['outcome_direction']=='FLAT' for o in valid)
        blockers=dict(sorted(Counter(o['outcome_status'] for o in sample if o['outcome_status']!='SCORED').items()))
        summary[f'{market}:{h}']={
          'candidates':len(sample),'predictions_issued':issued,
          'scored':len(valid),'correct':hits,'accuracy':hits/len(valid) if valid else None,
          'always_up_hits_on_same_scored_rows':baseline,
          'always_up_accuracy_on_same_scored_rows':baseline/len(valid) if valid else None,
          'flat_outcomes':flat,'unscorable_reasons':blockers,
          'score_coverage_of_candidates':len(valid)/len(sample) if sample else None,
        }
    return outcomes,summary


def write_jsonl(path,values):
    with path.open('w',encoding='utf-8',newline='\n') as f:
        for v in values:f.write(stable_json(v)+'\n')


def run(input_path,output_dir):
    input_path=Path(input_path); output_dir=Path(output_dir)
    rows=load_rows(input_path)
    output_dir.mkdir(parents=True,exist_ok=True)
    predictions=predict(rows)
    predictions_path=output_dir/'g2_first_forecast_predictions.jsonl'
    write_jsonl(predictions_path,predictions)
    # Prediction artifact is complete before outcome generation or score calls.
    outcomes,summary=score(predictions,rows)
    outcomes_path=output_dir/'g2_first_forecast_outcomes.jsonl'
    write_jsonl(outcomes_path,outcomes)
    summary_path=output_dir/'g2_first_forecast_summary.json'
    payload={'experiment_version':EXPERIMENT_VERSION,'feature_version':FEATURE_VERSION,
      'research_scope':'RECONSTRUCTED_DEVELOPMENT_ONLY_2021_2022',
      'historical_as_known_at_t0':False,'predictor':'sign(d5_atr at origin); zero/missing abstain; no fitted parameters',
      'comparator':'always UP, evaluated on exactly the same scored predictions',
      'outcome_reconstruction':'H5=future d5*ATR; H10=sum of two future d5*ATR; H20=future d20*ATR; only same-SECID exact calendar evidence',
      'limitations':['Overlapping label horizons; no independence assumed',
        'No proven vintage/as-known-at-T0; neither OOS nor paper/live performance',
        'Cross-contract future windows not scored; especially BR H20'],
      'input_sha256':sha(input_path), 'predictions_sha256':sha(predictions_path),
      'outcomes_sha256':sha(outcomes_path), 'input_rows':len(rows),
      'prediction_rows':len(predictions),'outcome_rows':len(outcomes),
      'per_market_horizon':summary}
    summary_path.write_text(stable_json(payload)+'\n',encoding='utf-8')
    return payload


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',required=True,type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args(argv)
    result=run(args.dataset,args.output_dir)
    print(stable_json({'input_rows':result['input_rows'],'prediction_rows':result['prediction_rows'],
      'markets':len(MARKETS),'summary_sha256':sha(args.output_dir/'g2_first_forecast_summary.json')}))


if __name__=='__main__':main()
