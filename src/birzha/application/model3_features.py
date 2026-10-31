"""Versioned RI/Purnov research features, independent of transport and Forecast.

The existing candle archive is reused. No future outcomes, network, trading,
calendar inference, or exact aggressor claims are part of these calculations.
"""
from __future__ import annotations
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import median
from zoneinfo import ZoneInfo

def _time(x):
    d=datetime.fromisoformat(x.replace("Z","+00:00"))
    return d.replace(tzinfo=ZoneInfo("Europe/Moscow")) if d.tzinfo is None else d

VERSION = "P3_OBS_FEATURES_V1_CONFIG_20261030"
BASE_COLUMNS = ("record_key symbol secid timeframe begin end source_record_key source_record_sha256 source_window_sha256 source_window_count window_start window_end formula_id calculated_at available_at available_at_confidence quality_status calendar_status source_complete_flag indicator_status return_5 return_10 return_20 sma_20 sma_50 efficiency_ratio_20 atr_14_pct atr_14_sma volume_ratio_20 vwap_20_candle_approx price_location_20 support_20_observed_min resistance_20_observed_max trend_score_legacy atr_14_wilder atr_14_wilder_status _atr_calc_key _atr_calc_time _atr_calc_contract _atr_calc_tr _atr_calc_valid _atr_calc_run_count _atr_calc_run_sum _atr_calc_value atr_model_id atr_specification_version atr_formula_id atr_algorithm_parameters_sha256 atr_source_record_key atr_source_hashes_json atr_calculation_window_id atr_quality_json _atr_calc_chain_start_key _atr_output_source_key").split()
EXTRA_COLUMNS = ["p3_"+x for x in ("range body upper_wick lower_wick close_location body_ratio upper_wick_ratio lower_wick_ratio close_progress abs_progress prior_atr14 range_prior_atr progress_prior_atr rvol_same_clock20_approx rvol_status prior_high prior_low overlap_points overlap_ratio up_extension down_extension effort_per_progress previous_move_retained_ratio confirmed_high_event confirmed_low_event pivot_event_time pivot_confirmation_time prior_confirmed_high prior_confirmed_low structure wave_anchor_time wave_anchor_price wave_progress wave_volume wave_bar_count up_level_test_count down_level_test_count failed_up_break failed_down_break candidate_state d1_prior_date d1_prior_close minute_count minute_sign_delta_proxy delta_proxy_status profile_poc_approx profile_val_approx profile_vah_approx m1_vwap_approx profile_status parameters_version signal_eligible profile_hvn_approx_json profile_lvn_approx_json cumulative_delta_proxy_civil_day").split()]
from birzha.application.continuous_archive_schema import CONTINUOUS_COLUMNS
LEGACY_COLUMNS = BASE_COLUMNS + EXTRA_COLUMNS
COLUMNS = LEGACY_COLUMNS + CONTINUOUS_COLUMNS
ATR_PARAMETERS_SHA = hashlib.sha256(b"ATR14_WILDER;seed=14_TR;require_prevclose;reset=incomplete_source").hexdigest()

def packed(x):
    return json.dumps(x,ensure_ascii=False,separators=(",",":"),allow_nan=False)

def sha(x):
    return hashlib.sha256(packed(x).encode()).hexdigest()

def number(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)

def research_features(candles, minute_features=None, *, targets=None, calculated_at=None):
    """Scan necessary state; materialize only requested missing/dependent outputs.

    All windows reset at contract boundaries. Sparse/unknown input remains
    research-only. targets contains source record keys, not positional indexes.
    """
    minute_features = minute_features or {}
    clock = calculated_at or datetime.now(timezone.utc).isoformat()
    daily=defaultdict(list); groups=defaultdict(list)
    for tf,table in candles.items():
        for values in table[1:]:
            r=dict(zip(table[0],values))
            groups[(r["contract_code"],tf)].append(r)
            if tf=="D1":daily[r["contract_code"]].append(r)
    for rows in daily.values():rows.sort(key=lambda r:_time(r["bar_start_time"]))
    result={tf:[] for tf in candles}
    for (sec,tf),rows in sorted(groups.items()):
        rows.sort(key=lambda r:_time(r["bar_start_time"]))
        atr=None; run=0; trsum=0; chain=""; highs=[]; lows=[]; anchor=None
        slots=defaultdict(lambda:deque(maxlen=20)); up_tests=dn_tests=0
        up_id=dn_id=None; was_up=was_dn=False; cumulative_volume=[0]; delta_sum=0; delta_day=None
        for i,r in enumerate(rows):
            o,h,l,c,v=(r[k] for k in ("open","high","low","close","volume"))
            if not all(number(x) for x in (o,h,l,c)) or not l<=min(o,c)<=max(o,c)<=h:raise ValueError("invalid OHLC")
            if v not in ("",None) and (not number(v) or v<0):raise ValueError("invalid volume")
            vol=v if number(v) else None
            p=rows[i-1] if i else None; pp=rows[i-2] if i>1 else None
            pc=p["close"] if p else None; progress=c-pc if p else None
            complete=r["is_complete"] is True and not (tf=="M15" and ("SPARSE" in str(r.get("source_kind","")) or (number(r.get("source_candles")) and r["source_candles"]<15))); prior_atr=atr; spread=h-l; body=abs(c-o)
            tr=max(spread,abs(h-pc),abs(l-pc)) if p else None
            if not complete:atr=None;run=0;trsum=0;chain=""
            elif not p or p["is_complete"] is not True:atr=None;run=0;trsum=0;chain=r["record_key"]
            else:
                run+=1;trsum+=tr
                if run==14:atr=trsum/14
                elif run>14:atr=(13*atr+tr)/14
            slot="D1" if tf=="D1" else r["bar_start_time"][11:16]
            hist=slots[slot]; m=median(hist) if len(hist)==20 and all(number(x) for x in hist) else None
            rv=vol/m if vol is not None and m is not None and m>0 else None
            hist.append(vol)
            ph=highs[-1] if highs else None;pl=lows[-1] if lows else None
            kh=ph["price"] if ph else None;kl=pl["price"] if pl else None
            if up_id!=(ph["time"] if ph else None):up_tests=0;was_up=False;up_id=ph["time"] if ph else None
            if dn_id!=(pl["time"] if pl else None):dn_tests=0;was_dn=False;dn_id=pl["time"] if pl else None
            tu=kh is not None and l<=kh<=h;td=kl is not None and l<=kl<=h
            if tu and not was_up:up_tests+=1
            if td and not was_dn:dn_tests+=1
            was_up,was_dn=tu,td
            fu=h>kh and c<kh if kh is not None else None
            fd=l<kl and c>kl if kl is not None else None
            structure="WARMUP"
            if len(highs)>=2 and len(lows)>=2:
                structure="HH_HL" if highs[-1]["price"]>highs[-2]["price"] and lows[-1]["price"]>lows[-2]["price"] else "LH_LL" if highs[-1]["price"]<highs[-2]["price"] and lows[-1]["price"]<lows[-2]["price"] else "MIXED"
            he=le=et=None
            if i>=4:
                q=rows[i-2];neighbors=[rows[j] for j in (i-4,i-3,i-1,i)]
                if all(q["high"]>n["high"] for n in neighbors):he=q["high"]
                if all(q["low"]<n["low"] for n in neighbors):le=q["low"]
                if he is not None or le is not None:et=q["bar_start_time"]
            cumulative_volume.append(cumulative_volume[-1]+(vol or 0))
            # Unknown volume invalidates a wave total rather than manufacturing zero.
            wave_rows=rows[anchor["index"]:i+1] if anchor else []
            wave_v=(cumulative_volume[-1]-cumulative_volume[anchor["index"]]) if anchor and all(number(x["volume"]) for x in wave_rows) else None
            mi=minute_features.get((sec,tf,r["bar_start_time"][:19].replace("T"," ")))
            day=r["bar_start_time"][:10]
            if day!=delta_day:delta_day=day;delta_sum=0
            cd=None
            if mi:
                if vol is None or mi["volume"]!=vol:raise ValueError("minute volume conflicts with candle")
                delta_sum+=mi["delta"];cd=delta_sum
            state="WAIT"
            if fu:state="FAILED_UP_BREAK_CANDIDATE"
            elif fd:state="FAILED_DOWN_BREAK_CANDIDATE"
            elif rv is not None and prior_atr and rv>=1.5 and spread/prior_atr>=1.2:
                if spread and (c-l)/spread>=.75 and kh is not None and c>kh:state="BULL_INITIATIVE_CANDIDATE"
                elif spread and (c-l)/spread<=.25 and kl is not None and c<kl:state="BEAR_INITIATIVE_CANDIDATE"
                elif abs(progress or 0)/prior_atr<=.25:state="EFFORT_WITHOUT_RESULT_CANDIDATE"
            if not complete:state="INSUFFICIENT_SOURCE_QUALITY"
            if targets is None or r["record_key"] in targets:
                prev20=rows[max(0,i-20):i];win20=rows[max(0,i-19):i+1]
                def ret(n):return c/rows[i-n]["close"]-1 if i>=n and rows[i-n]["close"] else None
                def sma(n):return sum(x["close"] for x in rows[i+1-n:i+1])/n if i+1>=n else None
                path=sum(abs(rows[j]["close"]-rows[j-1]["close"]) for j in range(i-19,i+1)) if i>=20 else 0
                vb=sum(x["volume"] for x in prev20)/20 if len(prev20)==20 and all(number(x["volume"]) for x in prev20) else None
                low20=min(x["low"] for x in prev20) if len(prev20)==20 else None
                high20=max(x["high"] for x in prev20) if len(prev20)==20 else None
                vv=sum(x["volume"] for x in win20) if all(number(x["volume"]) for x in win20) else None
                d=next((d for d in reversed(daily[sec]) if d["calendar_date"]<r["calendar_date"]),None)
                overlap=max(0,min(h,p["high"])-max(l,p["low"])) if p else None
                pm=pc-pp["close"] if pp else None
                source_hash=r.get("normalized_sha256") or sha(r)
                z=dict(record_key=f"RI|{sec}|{tf}|{r['bar_start_time']}|P3_OBS_V1",symbol="RI",secid=sec,timeframe=tf,begin=r["bar_start_time"],end=r["bar_end_time"],
                    source_record_key=r["record_key"],source_record_sha256=source_hash,source_window_sha256=sha([x.get("normalized_sha256") or sha(x) for x in rows[max(0,i-50):i+1]]),
                    source_window_count=min(i+1,51),window_start=rows[max(0,i-50)]["bar_start_time"],window_end=r["bar_start_time"],formula_id="P3_OHLCV_RESEARCH_V1",calculated_at=clock,
                    available_at=r.get("available_at") or None,available_at_confidence=r.get("available_at_confidence") or "UNKNOWN",quality_status="RECONSTRUCTED_RESEARCH_ONLY",calendar_status="CALENDAR_UNVERIFIED",source_complete_flag=complete,indicator_status="RESEARCH_NOT_SIGNAL;WINDOWS_OBSERVED;FIRST_AVAILABLE_UNKNOWN",
                    return_5=ret(5),return_10=ret(10),return_20=ret(20),sma_20=sma(20),sma_50=sma(50),
                    efficiency_ratio_20=abs(c-rows[i-20]["close"])/path if i>=20 and path else None,
                    atr_14_pct=atr/c if atr is not None and c else None,volume_ratio_20=vol/vb if vol is not None and vb else None,
                    vwap_20_candle_approx=sum((x["high"]+x["low"]+x["close"])/3*x["volume"] for x in win20)/vv if len(win20)==20 and vv else None,
                    price_location_20=(c-low20)/(high20-low20) if high20 is not None and high20>low20 else None,support_20_observed_min=low20,resistance_20_observed_max=high20,
                    atr_14_wilder=atr,atr_14_wilder_status="RESEARCH_VALUE" if atr is not None else "WARMUP_OR_SOURCE_INCOMPLETE",atr_model_id="P3_PURNOV_BAR_RESEARCH",atr_specification_version="OBS_FEATURES_V1",atr_formula_id="WILDER14_PREVCLOSE_SEED14_TR_RESET_INCOMPLETE",
                    atr_algorithm_parameters_sha256=ATR_PARAMETERS_SHA,atr_source_record_key=r["record_key"],atr_source_hashes_json=packed({"source":r.get("normalized_sha256") or None,"archive":r.get("source_archive_sha256") or None}),
                    atr_calculation_window_id=chain or r["record_key"],atr_quality_json="FIRST_AVAILABLE_UNKNOWN;CALENDAR_UNVERIFIED",_atr_calc_chain_start_key=chain or r["record_key"],_atr_output_source_key=r["record_key"])
                ex=[spread,body,h-max(o,c),min(o,c)-l,(c-l)/spread if spread else None,body/spread if spread else None,(h-max(o,c))/spread if spread else None,(min(o,c)-l)/spread if spread else None,progress,abs(progress) if progress is not None else None,prior_atr,spread/prior_atr if prior_atr else None,progress/prior_atr if prior_atr and progress is not None else None,
                    rv,"WARMUP20" if m is None else "PAST20_SAME_CONTRACT" if tf=="D1" else "SAME_CIVIL_CLOCK_APPROX_SESSION_UNVERIFIED",
                    p["high"] if p else None,p["low"] if p else None,overlap,overlap/spread if spread and p else None,
                    max(0,h-p["high"]) if p else None,max(0,p["low"]-l) if p else None,vol/abs(progress) if vol is not None and progress else None,(c-pp["close"])/pm if pm else None,
                    he,le,et,r["bar_end_time"] if et else None,kh,kl,structure,anchor["time"] if anchor else None,anchor["price"] if anchor else None,c-anchor["price"] if anchor else None,wave_v,i-anchor["index"]+1 if anchor else None,
                    up_tests if kh is not None else None,dn_tests if kl is not None else None,fu,fd,state,d["calendar_date"] if d else None,d["close"] if d else None,
                    mi["count"] if mi else None,mi["delta"] if mi else None,"M1_CLOSE_SIGN;TIES_ZERO;RESET_CIVIL_DATE;NOT_AGGRESSOR" if mi else "UNAVAILABLE",
                    mi["poc"] if mi else None,mi["val"] if mi else None,mi["vah"] if mi else None,mi["vwap"] if mi else None,
                    "UNIFORM_M1_LOW_HIGH_GRID100;BAR_WINDOW;VA70;LOWER_TIE;APPROX" if mi else "UNAVAILABLE",VERSION,False,
                    packed(mi["hvn"]) if mi else None,packed(mi["lvn"]) if mi else None,cd]
                z.update(zip(EXTRA_COLUMNS,ex));result[tf].append([z.get(k,"") if z.get(k) is not None else "" for k in COLUMNS])
            if he is not None:highs.append(dict(price=he,time=et,index=i-2))
            if le is not None:lows.append(dict(price=le,time=et,index=i-2))
            if he is not None and le is None:anchor=highs[-1]
            if le is not None and he is None:anchor=lows[-1]
    return result
