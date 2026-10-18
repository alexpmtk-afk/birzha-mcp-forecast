"""Optional derived sheets in the existing sole-writer mirror; no new collector.

V2 identities include source and window revisions. Frozen V1 records are retained.
All inputs are reconstructed observations, never a historical forecast admission.
"""
from __future__ import annotations
import dataclasses
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from birzha.application.canonical_market_mirror import MirrorConflict, MAX_ROWS, digest, rectangular, sheet_timestamp, validate_identity
from birzha.application.features import TimeframeFeatureEngine
from birzha.domain.market import Candle, CandleSeries, Instrument

INDICATOR_COLUMNS = ['record_key','symbol','secid','timeframe','begin','end','source_record_key','source_record_sha256','source_window_sha256','source_window_count','window_start','window_end','formula_id','calculated_at','available_at','available_at_confidence','quality_status','calendar_status','source_complete_flag','indicator_status','return_5','return_10','return_20','sma_20','sma_50','efficiency_ratio_20','atr_14_pct','atr_14_sma','volume_ratio_20','vwap_20_candle_approx','price_location_20','support_20_observed_min','resistance_20_observed_max','trend_score_legacy']
FLOW_COLUMNS = ['record_key','symbol','secid','underlying','scope_type','timeframe','event_time','logical_begin','trade_session_date','source_record_key','source_hash','buy_volume','sell_volume','unknown_volume','total_volume','delta','delta_ratio','cumulative_delta','reset_rule','volume_unit','available_at','observed_at','first_seen_at','publication_time','confidence','quality_status','methodology','raw_payload_ref']
POSITION_COLUMNS = ['record_key','symbol','secid','underlying','scope_type','source_record_key','source_hash','event_time','trade_session_date','oi','oi_change_observed','clgroup','pos_long','pos_short','pos_net','publication_time','observed_at','available_at','confidence','units','quality_status','raw_payload_ref']
HEADERS = {'INDICATORS': INDICATOR_COLUMNS, 'FLOW': FLOW_COLUMNS, 'POSITIONS': POSITION_COLUMNS}
FEATURES = {'return_5':('return_5',6),'return_10':('return_10',11),'return_20':('return_20',21),'sma_20':('sma_20',20),'sma_50':('sma_50',50),'efficiency_ratio_20':('efficiency_ratio_20',21),'atr_14_pct':('atr_14_pct',15),'volume_ratio_20':('volume_ratio_20',21),'vwap_20_candle_approx':('vwap_20',20),'price_location_20':('price_location_20',20),'support_20_observed_min':('support_20',20),'resistance_20_observed_max':('resistance_20',20),'trend_score_legacy':('trend_score',50)}

def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def sha(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()

def number(value):
    return value if isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) else None

def formula_version():
    feature_file = Path(__file__).with_name('features.py')
    return 'MAIN_FEATURES_' + hashlib.sha256(feature_file.read_bytes()).hexdigest() + '_COMPLETE_EXACT_CONTRACT_REVISION_WINDOWS_V2'

def indicator_rows(symbol, candles, *, calculated_at=None):
    version = formula_version()
    formula_id = hashlib.sha256(version.encode()).hexdigest()[:16]
    clock = calculated_at or datetime.now(timezone.utc).isoformat()
    groups = defaultdict(list)
    for tf, rows in candles.items():
        rows = rectangular(rows)
        validate_identity(rows, symbol.upper(), tf, incoming=False)
        header = rows[0]
        for values in rows[1:]:
            raw = dict(zip(header, values))
            groups[(str(raw['contract_code']), tf)].append(raw)
    output=[]
    engine = TimeframeFeatureEngine()
    for (secid,tf), rows in sorted(groups.items()):
        window=[]; lineage=[]; seen=set()
        instrument=Instrument(symbol=symbol,secid=secid,board='',engine='',market='',asset_class='index' if symbol in {'IMOEX','RTSI'} else 'equity' if symbol=='SBER' else 'future')
        from birzha.application.home_market_mirror import _stamp
        for raw in sorted(rows,key=lambda r:_stamp(r['bar_start_time'])):
            start=_stamp(raw['bar_start_time']);end=_stamp(raw['bar_end_time'])
            if start in seen: raise MirrorConflict('duplicate semantic derived source identity')
            seen.add(start)
            if end < start: raise MirrorConflict('invalid derived source interval')
            source_hash=sha(raw)
            valid=all(number(raw[k]) is not None for k in ('open','high','low','close'))
            valid=valid and raw['low']<=min(raw['open'],raw['close'])<=max(raw['open'],raw['close'])<=raw['high']
            eligible=valid and raw['is_complete'] is True
            if eligible:
                window.append(Candle(open=raw['open'],high=raw['high'],low=raw['low'],close=raw['close'],volume=number(raw.get('volume')),value=number(raw.get('turnover')),begin=start.isoformat(),end=end.isoformat(),completed=True))
                lineage.append(source_hash);window=window[-51:];lineage=lineage[-51:]
            else: window=[];lineage=[]
            window_hash=sha(lineage)
            record=dict(record_key=sha(['CANONICAL_DERIVED_V2',symbol,secid,tf,raw['record_key'],source_hash,window_hash,version]),symbol=symbol,secid=secid,timeframe=tf,begin=start.isoformat(),end=end.isoformat(),source_record_key=raw['record_key'],source_record_sha256=source_hash,source_window_sha256=window_hash,source_window_count=len(window),window_start=window[0].begin if window else None,window_end=window[-1].end if window else None,formula_id=formula_id,calculated_at=clock,available_at=None,available_at_confidence='UNKNOWN',quality_status='RECONSTRUCTED_RESEARCH_ONLY',calendar_status='UNVERIFIED',source_complete_flag=raw['is_complete'])
            state=dataclasses.asdict(engine.build(CandleSeries(instrument,tf,tuple(window)))) if window else {}
            statuses={}
            for output_name,(field,needed) in FEATURES.items():
                value=None
                if not eligible: status='SOURCE_INCOMPLETE' if valid else 'INVALID_SOURCE_PRICE'
                elif len(window)<needed: status='INSUFFICIENT_HISTORY'
                elif output_name in {'volume_ratio_20','vwap_20_candle_approx'} and symbol in {'IMOEX','RTSI'}: status='NOT_APPLICABLE_INDEX_VOLUME'
                elif output_name in {'volume_ratio_20','vwap_20_candle_approx'} and any(c.volume is None or c.volume<0 for c in window[-needed:]): status='MISSING_VOLUME_IN_WINDOW'
                else:
                    value=state.get(field)
                    status='RECONSTRUCTED_RESEARCH_ONLY' if value is not None else 'UNAVAILABLE_SOURCE_OR_ZERO_DENOMINATOR'
                record[output_name]=value;statuses[output_name]=status
            record['atr_14_sma']=record['atr_14_pct']*raw['close'] if record['atr_14_pct'] is not None else None
            statuses['atr_14_sma']=statuses['atr_14_pct'];record['indicator_status']=packed(statuses)
            output.append([record.get(k) for k in INDICATOR_COLUMNS])
    return output, version

def flow_rows(symbol, source_rows):
    """Whitelisted stored source fields only. No raw payload is published.

    Partial M5 delta uses only classified volume in its explicitly named ratio.
    Neither a full-session cumulative delta nor first-receipt time is invented.
    """
    import re
    flow=[];positions=[]
    for source in source_rows:
        dataset=source['dataset'];key=source['key_symbol'];raw=source['payload']
        expected={'SI':'Si','Si':'Si','BR':'BR','GOLD':'GD'}.get(symbol)
        if dataset!='FUTOI':
            pattern=(expected+r'[FGHJKMNQUVXZ][0-9]{1,2}') if expected else 'SBER' if symbol=='SBER' else ''
            if not pattern or re.fullmatch(pattern,key,re.IGNORECASE) is None:
                raise MirrorConflict('stored flow exact contract does not match market')
        source_hash=sha(raw)
        ref='historical_flow_rows:'+packed([dataset,key,source['row_key'],source_hash])
        fields=dict(symbol=symbol,secid=key if dataset!='FUTOI' else None,underlying=None,scope_type='EXACT_CONTRACT_CAPTURED_SUBSET',source_record_key=source['row_key'],source_hash=source_hash,event_time=None,trade_session_date=None,available_at=source.get('available_at'),observed_at=source.get('observed_at'),confidence=source.get('available_at_confidence') or 'UNKNOWN',quality_status='STORED_PARTIAL_RESEARCH_ONLY',raw_payload_ref=ref)
        def emit(target,kind,extra):
            record={**fields,**extra}
            record['record_key']=sha(['STORED_FLOW_V2',symbol,dataset,key,source['row_key'],source_hash,kind])
            columns=FLOW_COLUMNS if target is flow else POSITION_COLUMNS
            target.append([record.get(k) for k in columns])
        if dataset=='FUTOI':
            ticker=raw.get('ticker')
            if not expected or str(ticker).upper()!=expected.upper():
                raise MirrorConflict('FUTOI underlying does not match requested market')
            long,short,net=map(number,(raw.get('pos_long'),raw.get('pos_short'),raw.get('pos')))
            if all(x is not None for x in (long,short,net)) and long+short!=net:
                raise MirrorConflict('FUTOI signed positions mismatch')
            emit(positions,'FUTOI',dict(underlying=ticker,scope_type='UNDERLYING_ALL_CONTRACTS',event_time=_event(raw,'tradedate','tradetime'),publication_time=raw.get('systime'),clgroup=raw.get('clgroup'),pos_long=long,pos_short=short,pos_net=net,units='SOURCE_NATIVE_UNIT_UNVERIFIED'))
        elif dataset=='TRADESTATS':
            b,s=map(number,(raw.get('vol_b'),raw.get('vol_s')))
            if any(x is not None and x<0 for x in (b,s)): raise MirrorConflict('negative classified volume')
            total=b+s if b is not None and s is not None else None
            delta=b-s if total is not None else None
            emit(flow,'M5_DELTA',dict(timeframe='M5',event_time=_event(raw,'tradedate','tradetime'),buy_volume=b,sell_volume=s,total_volume=total,delta=delta,delta_ratio=delta/total if total else None,reset_rule='NO_FULL_SESSION_CUMULATIVE_DELTA',volume_unit='SOURCE_NATIVE_UNIT_UNVERIFIED',methodology='CLASSIFIED_BUY_MINUS_SELL;RATIO_DENOMINATOR_CLASSIFIED_VOLUME;UNKNOWN_VOLUME_UNAVAILABLE;PUBLIC_DERIVED_NOT_SUBSCRIPTION_ATTESTATION'))
        elif dataset=='PUBLIC_TRADES_RAW':
            oi=number(raw.get('OPENPOSITION'))
            if oi is not None:
                if symbol=='SBER': raise MirrorConflict('equity OI is not applicable')
                emit(positions,'CAPTURED_OI',dict(event_time=_event(raw,'TRADEDATE','TRADETIME'),oi=oi,units='SOURCE_NATIVE_UNIT_UNVERIFIED',quality_status='CAPTURED_TRADE_OI_NO_CONTINUOUS_SESSION_CLAIM'))
        else: raise MirrorConflict('unsupported derived source dataset')
    return {'FLOW':flow,'POSITIONS':positions}

def _event(raw, day, clock):
    if not raw.get(day) or not raw.get(clock):
        raise MirrorConflict('stored flow has no event clock')
    value=str(raw[day])+'T'+str(raw[clock])
    try: datetime.fromisoformat(value)
    except ValueError: raise MirrorConflict('invalid stored flow event clock') from None
    return value

def derived_rectangle(rows):
    if not rows or not rows[0] or len(rows)>MAX_ROWS: raise MirrorConflict('missing derived header or bounded row limit')
    width=len(rows[0])
    if not 1<=width<=40: raise MirrorConflict('invalid derived width')
    result=[]
    for row in rows:
        if len(row)>width: raise MirrorConflict('derived row exceeds header')
        normalized=[x if x is not None else '' for x in row]+['']*(width-len(row))
        for value in normalized:
            if isinstance(value,bool): continue
            if isinstance(value,(int,float)) and math.isfinite(value): continue
            if isinstance(value,str) and len(value)<=45000 and not value.startswith('='): continue
            raise MirrorConflict('invalid existing derived literal')
        result.append(normalized)
    return result

def plan_rows(sheet, existing, incoming):
    header=HEADERS[sheet]
    old=derived_rectangle(existing) if existing else []
    if not old or old[0]!=header:
        raise MirrorConflict('existing derived sheet header required; no implicit creation')
    ignored=header.index('calculated_at') if sheet=='INDICATORS' else -1
    def normalized(row):
        return [None if x=='' else x for i,x in enumerate(row) if i!=ignored]
    index={}
    for row in old[1:]:
        if not isinstance(row[0],str) or not row[0] or row[0] in index: raise MirrorConflict('invalid or duplicate derived key')
        index[row[0]]=row
    added=[]
    for row in incoming:
        if len(row)!=len(header) or not isinstance(row[0],str) or not row[0]: raise MirrorConflict('invalid derived row')
        for value in row:
            if value is None or isinstance(value,bool): continue
            if isinstance(value,(float,int)) and math.isfinite(value): continue
            if isinstance(value,str) and not value.startswith('=') and len(value)<=45000: continue
            raise MirrorConflict('invalid derived literal')
        if len(packed(row).encode())>15000: raise MirrorConflict('derived row exceeds bounded transfer size')
        prior=index.get(row[0])
        if prior is not None:
            if digest([normalized(prior)])!=digest([normalized(row)]): raise MirrorConflict('derived key has conflicting values')
        else:
            index[row[0]]=row;added.append([x if x is not None else '' for x in row])
    if len(old)+len(added)>MAX_ROWS: raise MirrorConflict('derived sheet exceeds bounded grid')
    return old, added

def append_batches(rows):
    """Exact native row encoding, below the 1 MB transport cap before any write."""
    chunks=[];chunk=[];size=0
    for row in rows:
        cells=[]
        for value in row:
            if value in (None,''): cells.append({})
            elif isinstance(value,bool): cells.append({'userEnteredValue':{'boolValue':value}})
            elif isinstance(value,(int,float)): cells.append({'userEnteredValue':{'numberValue':value}})
            else: cells.append({'userEnteredValue':{'stringValue':value}})
        row_size=len(json.dumps({'values':cells},ensure_ascii=False,allow_nan=False).encode())+2
        if row_size>650000: raise MirrorConflict('derived row exceeds native transfer bound')
        if chunk and (size+row_size>650000 or len(chunk)>=250):
            chunks.append(chunk);chunk=[];size=0
        chunk.append(row);size+=row_size
    if chunk:chunks.append(chunk)
    return chunks

def prepare(transport,symbol,candles,flow_source,from_date,till_date):
    if flow_source is None: raise MirrorConflict('existing flow store required for derived update')
    indicators,version=indicator_rows(symbol,candles)
    incoming=flow_rows(symbol,flow_source.export_mirror_bundle(symbol,from_date,till_date))
    incoming['INDICATORS']=indicators
    plans={}
    for sheet in HEADERS:
        transport.validate_derived_target(symbol,sheet)
        old,additions=plan_rows(sheet,transport.read_rows(symbol,sheet),incoming[sheet])
        append_batches(additions)  # All payload sizes validated in whole-book preflight.
        plans[sheet]=(old,additions)
    return plans,version

def publish(transport,symbol,plans):
    """No blind retry. A fresh invocation reconciles any committed partial prefix."""
    result={}
    for sheet,(old,additions) in plans.items():
        transport.validate_derived_target(symbol,sheet)
        current=derived_rectangle(transport.read_rows(symbol,sheet))
        if digest(current)!=digest(old): raise MirrorConflict('derived destination changed since preflight')
        for chunk in append_batches(additions):
            transport.append_rows(symbol,sheet,chunk)
        transport.validate_derived_target(symbol,sheet)
        after=derived_rectangle(transport.read_rows(symbol,sheet))
        if digest(after)!=digest(old+additions): raise MirrorConflict('derived post-write readback mismatch')
        result[sheet]={'added_rows':len(additions),'data_rows':len(after)-1,'sha256':digest(after),'scope':'RESEARCH_ONLY_PARTIAL_NOT_FORECAST_ADMISSION'}
    for sheet,summary in result.items():
        transport.validate_derived_target(symbol,sheet)
        if digest(derived_rectangle(transport.read_rows(symbol,sheet)))!=summary['sha256']:
            raise MirrorConflict('final cross-derived-sheet digest mismatch')
    return result