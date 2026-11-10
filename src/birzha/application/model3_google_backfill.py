"""Cloud-only RI research indicator planner for the already-approved 95-column layout.

This module does not access the network or mutate cells. Only completed,
source-pinned research candles are used. Output is NOT a trading signal.
"""
from __future__ import annotations

from datetime import date, datetime

from birzha.application.model3_features import research_features, number
from birzha.application.model3_indicator_coverage import (
    COMPACT_RI_INDICATOR_COLUMNS, COMPACT_HEADERS, coverage_summary, project_calculated_rows,
)
from birzha.application.canonical_market_mirror import MirrorConflict, digest
from birzha.application.google_first_archive import stamp, packed


def _ordered(source, timefield, contractfield):
    head = source[0]
    ti, si = head.index(timefield), head.index(contractfield)
    def sort_key(row):
        return (stamp(row[ti]), str(row[si]))
    return sorted(source[1:], key=sort_key)


def plan_ri_indicator_backfill(*, candles, daily, existing, timeframe,
                               from_date, till_date, max_rows, calculated_at,
                               minute_features=None):
    """Return an exact, bounded insertion plan plus complete rows.

    New output uses only prior source candles, always with research-only labels.
    Existing 2026 research rows are never recalculated/overwritten by this path.
    New rows are appended after all existing rows; no existing address moves.
    """
    if timeframe not in ('D1', 'H1', 'M15'):
        raise MirrorConflict('MODEL3_TIMEFRAME_UNSUPPORTED')
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or not 1 <= max_rows <= 50:
        raise MirrorConflict('MODEL3_MAX_ROWS_BOUNDED_50')
    start, finish = date.fromisoformat(from_date), date.fromisoformat(till_date)
    if start < date(2025, 1, 1) or not 0 <= (finish - start).days <= 30:
        raise MirrorConflict('MODEL3_DATE_RANGE_BOUNDED_31')
    if not isinstance(calculated_at, str) or not calculated_at:
        raise MirrorConflict('MODEL3_CALCULATED_AT_REQUIRED')
    if not candles or not daily or not existing:
        raise MirrorConflict('MODEL3_INPUT_REQUIRED')
    if tuple(existing[0]) not in COMPACT_HEADERS:
        raise MirrorConflict('MODEL3_COMPACT_95_ONLY')
    coverage = coverage_summary(candles, existing, timeframe=timeframe)
    col = {name: candles[0].index(name) for name in ('record_key', 'bar_start_time', 'contract_code', 'is_complete')}
    ih = existing[0]
    source_col = ih.index('source_record_key')
    begin_col = ih.index('begin')
    secid_col = ih.index('secid')
    known = {str(r[source_col]) for r in existing[1:]}
    # Appending preserves existing row addresses even for a contract-grouped
    # legacy table. Calculations are still ordered by exact source timestamps.
    selected = []
    for row in _ordered(candles, 'bar_start_time', 'contract_code'):
        when = stamp(row[col['bar_start_time']]).date()
        if start <= when <= finish and row[col['record_key']] not in known:
            selected.append(row)
    if not selected:
        return {'status': 'MODEL3_NO_MISSING_IN_WINDOW', 'written': False, 'planned_rows': 0,
                'coverage': coverage, 'insert_row_index': None}, []
    chosen = selected[:max_rows]
    chosen_keys = {r[col['record_key']] for r in chosen}

    def cutoff(table):
        h = table[0]
        ti = h.index('bar_start_time')
        return [h] + [r for r in table[1:]
                      if stamp(r[ti]).date() <= finish]
    inputs = {'D1': cutoff(daily)}
    if timeframe != 'D1':
        inputs[timeframe] = cutoff(candles)
    # Reuse the already-versioned research implementation; never invent delta,
    # exact volume-at-price, availability timestamps or session/calendar labels.
    generated = research_features(inputs, minute_features=minute_features,
                                  targets=chosen_keys, calculated_at=calculated_at)
    projected = project_calculated_rows(generated[timeframe], existing[0])
    by_source = {r[source_col]: r for r in projected}
    if len(by_source) != len(chosen) or set(by_source) != chosen_keys:
        raise MirrorConflict('MODEL3_GENERATED_SOURCE_KEYS_MISMATCH')
    output = [by_source[r[col['record_key']]] for r in chosen]
    if any(len(r) != len(ih) for r in output):
        raise MirrorConflict('MODEL3_ROW_WIDTH_MISMATCH')
    return {'status': 'MODEL3_RESEARCH_BACKFILL_PLAN', 'written': False,
            'timeframe': timeframe, 'planned_rows': len(output),
            'remaining_window_records': len(selected) - len(output),
            'insert_row_index': len(existing),  # compatibility field: append offset, incl. header
            'write_mode': 'APPEND_ONLY_NO_ROW_SHIFT',
            'source_keys': [r[col['record_key']] for r in chosen],
            'row_digest': digest([list(existing[0])]+output),
            'coverage': coverage, 'forecast_admission': 'NOT_GRANTED',
            'calendar': 'UNVERIFIED', 'historical_availability': 'UNKNOWN',
            'minute_features': 'APPROX_SOURCE_M1' if minute_features else 'UNAVAILABLE',
            'indicator_columns': len(ih), 'calculated_at': calculated_at}, output


def minute_features_from_existing_drive(*, transport, candles, timeframe,
                                        source_keys, till_date):
    """Read pinned RI monthly M1 ZIP(s), using the existing Drive transport.

    This is *never* exact aggressor Delta or tick-by-tick price volume.
    The underlying published M1 captures remain independent research evidence.
    """
    if timeframe not in ("H1", "M15"):
        raise MirrorConflict("MODEL3_MINUTE_PROFILE_INTRADAY_ONLY")
    from birzha.application.model3_minutes import clock, minute_groups, profiles
    from birzha.storage.google_m1_bytes import validate_zip
    from birzha.application.google_first_archive import stamp, packed
    h = candles[0]
    idx={k:h.index(k) for k in ("record_key","bar_start_time","bar_end_time","contract_code","volume")}
    chosen = [r for r in candles[1:] if r[idx["record_key"]] in source_keys]
    if len(chosen)!=len(source_keys):
        raise MirrorConflict("MODEL3_MINUTE_SOURCE_KEY_MISSING")
    combos = sorted({(r[idx["contract_code"]],stamp(r[idx["bar_start_time"]]).strftime("%Y-%m"))
                    for r in chosen})
    if not 1<=len(combos)<=4:
        raise MirrorConflict("MODEL3_MINUTE_MONTH_CONTRACT_LIMIT")
    minutes={}
    evidence=[]
    days = {stamp(r[idx["bar_start_time"]]).date().isoformat() for r in chosen}
    for sec,month in combos:
        info=transport.read_m1("RI",sec,month)
        if not info or not info.get("indexed"):
            raise MirrorConflict("MODEL3_INDEXED_M1_REQUIRED")
        evidence.append({"secid":sec,"month":month,"sha256":info["sha256"],"file_id":info["file_id"]})
        for x in validate_zip(info["body"],"RI",sec,month,info["sha256"]):
            t=clock(x["begin"])
            if t[:10] not in days:continue
            key=(sec,t)
            if key in minutes:raise MirrorConflict("MODEL3_M1_DUPLICATE")
            minutes[key]={**x, "begin":t}
    groups=minute_groups(minutes,min(days),till_date)
    # Keep earlier bars of each selected civil day: cumulative delta must
    # be identical whether a day is written in one batch or several batches.
    # Re-key short opening bars (e.g.08:50 H1) to their actual source begin.
    context={}
    for r in candles[1:]:
        sec=r[idx["contract_code"]]
        begin=clock(r[idx["bar_start_time"]])
        if begin[:10] not in days or (sec,begin[:7]) not in combos:continue
        end=clock(r[idx["bar_end_time"]])
        bucket=(begin[:13]+":00:00" if timeframe=="H1" else
                begin[:14]+f"{int(begin[14:16])//15*15:02d}"+":00")
        rs=[x for x in groups.get((sec,timeframe,bucket),()) if begin<=x[0]<=end]
        if rs:context[(sec,timeframe,begin)]=rs
    result=profiles(context,set(context))
    for r in chosen:
        key=(r[idx["contract_code"]],timeframe,clock(r[idx["bar_start_time"]]))
        vol=r[idx["volume"]]
        if key not in result:
            if number(vol) and vol>0:raise MirrorConflict("MODEL3_M1_PROFILE_MISSING")
            continue
        if not number(vol) or result[key]["volume"]!=vol:
            raise MirrorConflict("MODEL3_M1_CANDLE_VOLUME_CONFLICT")
    return result, evidence

# Appended to the existing cloud planner: no network or alternate collector.
REPAIR_NUMERIC_FIELDS = tuple("""
return_5 return_10 return_20 sma_20 sma_50 efficiency_ratio_20 atr_14_pct
volume_ratio_20 vwap_20_candle_approx price_location_20 support_20_observed_min
resistance_20_observed_max atr_14_wilder
""".split())
REPAIR_FIELDS = REPAIR_NUMERIC_FIELDS + tuple(
    c for c in COMPACT_RI_INDICATOR_COLUMNS if c.startswith('p3_') and c not in {
        'p3_parameters_version', 'p3_signal_eligible', 'p3_rvol_status',
        'p3_delta_proxy_status', 'p3_profile_status',
    })


def scope_2026(candles, indicators):
    """Identical Moscow-year scope; never infer year from contract suffix."""
    ci = candles[0].index('bar_start_time')
    ii = indicators[0].index('source_record_key')
    return (
        [candles[0]] + [r for r in candles[1:] if stamp(r[ci]).year == 2026],
        [indicators[0]] + [r for r in indicators[1:] if stamp(str(r[ii]).split('|',3)[-1]).year == 2026],
    )


def plan_ri_field_repair(*, candles, daily, existing, timeframe, from_date,
                         till_date, max_rows=50, max_cells=50, fields=None,
                         after_key='', calculated_at):
    """Bounded blank-only research patch and a reason for EACH scoped gap.

    Raw history may predate 2026 for warmup; only existing 2026 rows are targets.
    No M1 fetching, source-hash fabrication, filled values or metadata changes.
    """
    from collections import Counter, defaultdict
    from birzha.application.model3_features import VERSION
    from birzha.application.model3_write_audit import normalized
    start, finish = date.fromisoformat(from_date), date.fromisoformat(till_date)
    if start.year != 2026 or finish.year != 2026 or not 0 <= (finish-start).days <= 30:
        raise MirrorConflict('MODEL3_REPAIR_2026_BOUNDED_31_DAYS')
    if type(max_rows) is not int or not 1 <= max_rows <= 5000:
        raise MirrorConflict('MODEL3_REPAIR_ROWS_BOUND')
    if type(max_cells) is not int or not 1 <= max_cells <= 200:
        raise MirrorConflict('MODEL3_REPAIR_CELLS_BOUND')
    fields = tuple(REPAIR_FIELDS if fields is None else fields)
    if not fields or len(set(fields)) != len(fields) or any(f not in REPAIR_FIELDS for f in fields):
        raise MirrorConflict('MODEL3_REPAIR_FIELDS_ALLOWLIST')
    coverage_summary(candles, existing, timeframe=timeframe)
    old = normalized(existing); ih = {c:i for i,c in enumerate(old[0])}
    ch = {c:i for i,c in enumerate(candles[0])}
    dh = {c:i for i,c in enumerate(daily[0])}
    groups = defaultdict(list); by_key = {}
    for values in _ordered(candles, 'bar_start_time', 'contract_code'):
        row = dict(zip(candles[0],values))
        if stamp(row['bar_start_time']).date() <= finish:
            by_key[row['record_key']] = row
            groups[row['contract_code']].append(row)
    positions = {}; prefix = {}
    for rows in groups.values():
        endmax = None; bad = missing_volume = 0
        for i,r in enumerate(rows):
            end = stamp(r['bar_end_time'])
            endmax = end if endmax is None else max(endmax,end)
            bad += not (r.get('is_complete') is True and bool(r.get('normalized_sha256')))
            missing_volume += not number(r.get('volume'))
            positions[r['record_key']] = rows,i
            prefix[r['record_key']] = endmax,bad,missing_volume

    targets = []
    for rownum, row in enumerate(old[1:],1):
        when = stamp(row[ih['begin']])
        if start <= when.date() <= finish:
            key = row[ih['source_record_key']]
            src = by_key.get(key)
            if (src is None or str(src['contract_code']) != row[ih['secid']]
                    or stamp(src['bar_start_time']) != when):
                raise MirrorConflict('MODEL3_REPAIR_ROW_SOURCE_IDENTITY')
            targets.append((key,rownum,row))
    targets.sort(key=lambda x:(stamp(x[2][ih['begin']]),x[0]))
    if after_key:
        keys = [x[0] for x in targets]
        if after_key not in keys:raise MirrorConflict('MODEL3_REPAIR_CURSOR_UNKNOWN')
        targets = targets[keys.index(after_key)+1:]
    remaining_rows = max(0,len(targets)-max_rows)
    targets = targets[:max_rows]
    keys = {k for k,_,_ in targets}
    source = [candles[0]] + [r for r in candles[1:] if stamp(r[ch['bar_start_time']]).date() <= finish]
    dailies = [daily[0]] + [r for r in daily[1:] if stamp(r[dh['bar_start_time']]).date() <= finish]
    inputs = {'D1':dailies}
    if timeframe != 'D1':inputs[timeframe] = source
    generated = research_features(inputs, targets=keys, calculated_at=calculated_at)
    calculated = {r[ih['source_record_key']]:r
                  for r in project_calculated_rows(generated[timeframe],old[0])}
    counts = Counter(); gaps = []; edits = []; fillable = 0

    def trusted(r):
        return r.get('is_complete') is True and bool(r.get('normalized_sha256'))

    for key,rownum,row in targets:
        src = by_key[key]; history,i = positions[key]
        output = calculated[key]
        for field in fields:
            col = ih[field]
            if row[col] is not None and row[col] != '':continue  # zero/False/formula are occupied
            value = output[col]
            category = 'MISSING_CALCULATION'; reason = 'RECALCULATED_FROM_SAVED_OHLCV'
            deps = [src]; need = 1; reset_by_source = False; recursive = False
            source_ref = 'GOOGLE:'+timeframe+';SECID='+src['contract_code']
            if any(s in field for s in ('minute_', 'profile_', 'm1_', 'cumulative_delta')):
                category='NOT_APPLICABLE' if timeframe=='D1' else 'SOURCE_UNAVAILABLE'
                reason='EXISTING_M1_PROXY_SPEC_INTRADAY_ONLY' if timeframe=='D1' else 'INDEXED_M1_EVIDENCE_NOT_IN_THIS_PLAN'
                source_ref='GOOGLE_DRIVE_ZIP:M1_SOURCE_INDEX;NOT_READ'
            elif field in ('p3_d1_prior_date','p3_d1_prior_close'):
                matches = [dict(zip(daily[0],r)) for r in dailies[1:]
                           if r[dh['contract_code']]==src['contract_code']
                           and str(r[dh['calendar_date']])<str(src['calendar_date'])
                           and stamp(r[dh['bar_end_time']])<=stamp(src['bar_end_time'])]
                deps += matches[-1:] if matches else []
                need = 2;source_ref='GOOGLE:D1;SAME_SECID;CLOSED_BEFORE_TARGET'
            elif field in ('atr_14_wilder','atr_14_pct','p3_prior_atr14','p3_range_prior_atr','p3_progress_prior_atr'):
                j=i
                while j>0 and history[j-1].get('is_complete') is True:j-=1
                deps=history[j:i+1]
                reset_by_source = j > 0 and history[j-1].get("is_complete") is not True
                need=16 if field.startswith('p3_') else 15
            elif field=='p3_rvol_same_clock20_approx':
                slot=lambda x:'D1' if timeframe=='D1' else str(x['bar_start_time'])[11:16]
                deps=[r for r in history[:i] if slot(r)==slot(src)][-20:]+[src];need=21
            elif field.startswith('return_'):
                need=int(field.rsplit('_',1)[1])+1;deps=history[max(0,i-need+1):i+1]
            elif field in ('sma_20','sma_50'):
                need=int(field.rsplit('_',1)[1]);deps=history[max(0,i-need+1):i+1]
            elif field in ('efficiency_ratio_20','volume_ratio_20','price_location_20','support_20_observed_min','resistance_20_observed_max'):
                need=21;deps=history[max(0,i-20):i+1]
            elif field=='vwap_20_candle_approx':
                need=20;deps=history[max(0,i-19):i+1]
            elif field in ('p3_range','p3_body','p3_upper_wick','p3_lower_wick',
                           'p3_close_location','p3_body_ratio','p3_upper_wick_ratio','p3_lower_wick_ratio'):
                pass
            elif field in ('p3_close_progress','p3_abs_progress','p3_prior_high','p3_prior_low',
                           'p3_overlap_points','p3_overlap_ratio','p3_up_extension','p3_down_extension',
                           'p3_effort_per_progress'):
                need=2;deps=history[max(0,i-1):i+1]
            elif field=='p3_previous_move_retained_ratio':
                need=3;deps=history[max(0,i-2):i+1]
            else:
                # Recursive pivots/waves/candidates require all saved prior state.
                deps=history[:i+1];recursive=True
            if category=='MISSING_CALCULATION':
                if not trusted(src) or row[ih['source_record_sha256']] != src.get('normalized_sha256'):
                    category='SOURCE_UNRELIABLE';reason='TARGET_INCOMPLETE_OR_UNPINNED_SOURCE'
                elif row[ih['source_complete_flag']] not in (True,'TRUE','true'):
                    category='SOURCE_UNRELIABLE';reason='INDICATOR_SOURCE_QUALITY_NOT_CONFIRMED'
                elif (prefix[key][0]>stamp(src['bar_end_time']) if recursive else any(stamp(r['bar_end_time'])>stamp(src['bar_end_time']) for r in deps)):
                    category='SOURCE_UNRELIABLE';reason='DEPENDENCY_NOT_CLOSED_AT_TARGET'
                elif stamp(src['bar_end_time'])>stamp(calculated_at):
                    category='SOURCE_UNRELIABLE';reason='TARGET_NOT_CLOSED_AT_CALCULATION'
                elif (prefix[key][1]>0 if recursive else any(not trusted(r) for r in deps)):
                    category='SOURCE_UNRELIABLE';reason='DEPENDENCY_INCOMPLETE_OR_UNPINNED'
                elif row[ih['formula_id']]!='P3_OHLCV_RESEARCH_V1' or row[ih['p3_parameters_version']]!=VERSION:
                    category='SOURCE_UNRELIABLE';reason='EXISTING_FORMULA_VERSION_UNVERIFIED'
                elif len(deps)<need:
                    category='SOURCE_UNRELIABLE' if reset_by_source else 'INSUFFICIENT_WARMUP'
                    reason='ATR_CHAIN_RESET_BY_INCOMPLETE_SOURCE' if reset_by_source else 'PRIOR_SAME_CONTRACT_WINDOW_TOO_SHORT'
                elif field in ('p3_rvol_same_clock20_approx','volume_ratio_20','vwap_20_candle_approx','p3_wave_volume','p3_effort_per_progress') and (prefix[key][2]>0 if recursive else any(not number(r.get('volume')) for r in deps)):
                    category='SOURCE_UNAVAILABLE';reason='SAVED_VOLUME_MISSING_NOT_ZERO'
                elif value is None or value=='':
                    # A non-event or undefined ratio is NOT a missing numeric zero.
                    category='NOT_APPLICABLE';reason='CONDITION_NOT_OBSERVED_OR_UNDEFINED_RATIO'
                elif isinstance(value,str) and value.startswith('='):
                    raise MirrorConflict('MODEL3_REPAIR_LITERAL_REQUIRED')
            entry={'secid':src['contract_code'],'date':stamp(src['bar_start_time']).date().isoformat(),
                   'timeframe':timeframe,'source_key':key,'field':field,'row_index':rownum,
                   'column_index':col,'category':category,'reason':reason,'source':source_ref,
                   'can_calculate':category=='MISSING_CALCULATION',
                   'dependency_count':len(deps),'required_count':need}
            if category=='MISSING_CALCULATION':
                entry['value']=value;fillable+=1
                if len(edits)<max_cells:edits.append((rownum,col,value))
            gaps.append(entry);counts[category]+=1
    signature=digest([[digest(candles),digest(daily),digest(old),timeframe,
                       from_date,till_date,packed(list(fields)),after_key,max_rows,max_cells,
                       "RI_BLANK_FIELDS_V1_20261106",packed(edits)]])
    return {'status':'MODEL3_FIELD_REPAIR_PLAN','written':False,'year':2026,'timeframe':timeframe,
            'plan_sha256':signature,'repair_version':'RI_BLANK_FIELDS_V1_20261106','fields':list(fields),'gaps':gaps,'category_counts':dict(counts),
            'fillable_cells':fillable,'planned_cells':len(edits),'remaining_fillable_cells':fillable-len(edits),
            'scanned_rows':len(targets),'remaining_rows':remaining_rows,
            'next_key':targets[-1][0] if remaining_rows and targets else '',
            'forecast_admission':'NOT_GRANTED','external_writer_exclusion':'REQUIRED_FOR_APPLY'},edits
