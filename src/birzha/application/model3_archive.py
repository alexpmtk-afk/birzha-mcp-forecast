"""One RI archive operation: reuse sources, repair order, update features, read back.

Extends the existing canonical mirror transport. No alternate book, scheduler,
Forecast/Outcome mutation, automatic write retry, or production DB import.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import math
import threading
import uuid

from birzha.application.canonical_market_mirror import MirrorConflict, digest, rectangular, index_rows, validate_identity
from birzha.application.home_market_mirror import _stamp, project_source
from birzha.application.model3_features import COLUMNS, BASE_COLUMNS, VERSION, research_features, sha
from birzha.application.model3_minutes import load_minutes, minute_groups, aggregate, profiles, clock

FRAMES=("D1","H1","M15")

def _rect(rows,width):
    if not rows or not rows[0] or len(rows)>100000 or len(rows[0])!=width:raise MirrorConflict("unexpected existing model3 schema")
    out=[]
    for row in rows:
        if len(row)>width:raise MirrorConflict("row exceeds existing header")
        row=list(row)+[""]*(width-len(row))
        for x in row:
            if isinstance(x,bool):continue
            if isinstance(x,(int,float)) and math.isfinite(x):continue
            if isinstance(x,str) and not x.startswith("=") and len(x)<=45000:continue
            raise MirrorConflict("formula/nonliteral in model3 table; preserve formula and require explicit compatible migration")
        out.append(row)
    return out

def _validate_candles(rows,tf):
    index_rows(rows);validate_identity(rows,"RI",tf,incoming=False)
    h=rows[0];seen=set()
    for r in rows[1:]:
        identity=(r[h.index("contract_code")],_stamp(r[h.index("bar_start_time")]))
        if identity in seen:raise MirrorConflict("duplicate semantic candle identity")
        seen.add(identity)
        if _stamp(r[h.index("bar_end_time")])<identity[1]:raise MirrorConflict("negative source interval")

def _ordered(rows,indicator=False):
    h=rows[0];tc=h.index("begin" if indicator else "bar_start_time");sc=h.index("secid" if indicator else "contract_code")
    permutation=sorted(range(len(rows)-1),key=lambda i:(_stamp(rows[i+1][tc]),rows[i+1][sc]))
    return [rows[0],*[rows[i+1] for i in permutation]],permutation

def _same(a,b):
    if isinstance(a,bool) or isinstance(b,bool):return type(a) is type(b) and a==b
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):return math.isclose(a,b,rel_tol=1e-11,abs_tol=1e-9)
    return a==b

def _changed_source_targets(candles,old):
    """A frozen formula row is computed, including legitimate null/warm-up cells."""
    index={}
    for tf,rows in old.items():
        for r in rows[1:]:
            if r[6] in index:raise MirrorConflict("duplicate feature source identity")
            index[r[6]]=r
    targets=set();earliest={}
    for tf,rows in candles.items():
        h=rows[0]
        for r in rows[1:]:
            key=r[h.index("record_key")];sec=r[h.index("contract_code")];begin=_stamp(r[h.index("bar_start_time")])
            prior=index.get(key)
            if prior is None or len(prior)<=104 or prior[104]!=VERSION:
                targets.add(key);earliest[(sec,tf)]=min(begin,earliest.get((sec,tf),begin))
    # A backfill alters dependent windows and structural state, even if later cells are populated.
    for tf,rows in candles.items():
        h=rows[0]
        for r in rows[1:]:
            sec=r[h.index("contract_code")];begin=_stamp(r[h.index("bar_start_time")])
            if (sec,tf) in earliest and begin>=earliest[(sec,tf)]:targets.add(r[h.index("record_key")])
    return targets,index

def _restore_m15(table,groups):
    h=table[0];existing={(r[h.index("contract_code")],clock(r[h.index("bar_start_time")])) for r in table[1:]}
    additions=[]
    for (sec,tf,t),rs in sorted(groups.items()):
        if tf!="M15" or (sec,t) in existing:continue
        from birzha.providers.moex_iss import MoexIssClient
        from birzha.domain.market import Candle
        source=tuple(Candle(open=r["open"],high=r["high"],low=r["low"],close=r["close"],volume=r["volume"],value=r.get("value"),begin=t0,end=r["end"]) for t0,r,delta in rs)
        bar=MoexIssClient._aggregate_m15(source)[0]
        a=dict(open=bar.open,high=bar.high,low=bar.low,close=bar.close,volume=bar.volume,count=bar.observed_m1_count)
        start=_stamp(bar.begin);end=_stamp(bar.end)
        if end>=datetime.now(timezone.utc):continue
        key=f"RI|M15|{sec}|{start.isoformat()}"
        record=dict(record_key=key,calendar_date=t[:10],trade_session_date="",session_id="",bar_start_time=start.isoformat(),bar_end_time=end.isoformat(),
            available_at="",available_at_confidence="UNKNOWN",contract_code=sec,**{k:a[k] for k in ("open","high","low","close","volume")},
            number_of_trades="",turnover="",is_complete=bar.completed,source_interval="M1",source_candles=a["count"],source_id="SAVED_MOEX_M1_ARCHIVE",
            research_status="RECONSTRUCTED_RESEARCH_ONLY",normalized_sha256=sha([key,a]),source_archive_sha256=rs[0][1].get("source_archive_sha256",""),
            capture_day_status="OBSERVED_BUCKET_CALENDAR_UNVERIFIED",source_kind="DERIVED_M1_"+str(bar.observation_quality),
            caveat="M1_OBSERVED_NOT_FULL_SESSION_ATTESTATION;DENSITY_NOT_COMPLETION;PIT_UNKNOWN",source_capture_label="CONFIGURED_HASH_VERIFIED_LOCAL_ARCHIVE",source_file_id="")
        if set(h)-set(record):raise MirrorConflict("minute restore cannot populate unknown candle columns")
        additions.append([record[k] for k in h]);existing.add((sec,t))
    return additions

@dataclass
class Model3ArchiveUpdater:
    transport: object
    minute_archive_dir: str | None = None
    audit_dir: str | None = None
    source: object | None = None
    source_sha: str = "UNKNOWN"
    lock: object = field(default_factory=threading.Lock)

    def update(self,*,from_date:str,till_date:str,dry_run:bool=True,exclusive_workbook_owner:bool=False,repair_dependent:bool=False):
        start,end=date.fromisoformat(from_date),date.fromisoformat(till_date)
        if from_date!=start.isoformat() or till_date!=end.isoformat() or not 0<=(end-start).days<367:raise MirrorConflict("bounded exact date range required")
        if not dry_run and exclusive_workbook_owner is not True:raise MirrorConflict("whole-workbook sole writer required")
        if not self.lock.acquire(blocking=False):raise MirrorConflict("another mirror/model3 update is active")
        mutated=False
        try:
            before={};candles={};old_features={}
            for tf in FRAMES:
                for sheet in (tf,tf+"_INDICATORS"):
                    self.transport.validate_model3_target("RI",sheet)
                    data=self.transport.read_rows("RI",sheet)
                    if not data:raise MirrorConflict("existing candle/indicator headers required; no new sheets")
                    before[sheet]=data
                raw=rectangular(before[tf]);_validate_candles(raw,tf)
                candles[tf]=raw
                h=before[tf+"_INDICATORS"][0]
                if h not in (BASE_COLUMNS,COLUMNS):raise MirrorConflict("existing 54/109 indicator header required")
                old_features[tf]=_rect(before[tf+"_INDICATORS"],len(h))
                ids=[r[0] for r in old_features[tf][1:]]
                if any(not isinstance(k,str) or not k for k in ids) or len(set(ids))!=len(ids):
                    raise MirrorConflict("duplicate or invalid indicator key")
            self.transport.validate_service_target("RI","SYNC_STATUS");self.transport.read_status("RI")
            source_report="EXISTING_RESEARCH_SHEETS_ONLY"
            if self.source is not None:
                bundle=self.source.export_mirror_bundle("RI",from_date,till_date)
                if bundle["symbol"]!="RI" or bundle["backend"]!="DuckDB":raise MirrorConflict("unexpected existing source bundle")
                for tf in FRAMES:
                    incoming,_=project_source(bundle,tf,candles[tf])
                    keys={r[0] for r in candles[tf][1:]}
                    candles[tf]+=[r for r in incoming[1:] if r[0] not in keys]
                source_report="EXISTING_STORE_PLUS_PRESERVED_RESEARCH_ARCHIVE"
            minutes,minute_status=load_minutes(self.minute_archive_dir,"0001-01-01",till_date)
            groups=minute_groups(minutes,"0001-01-01",till_date)
            restore_groups={k:v for k,v in groups.items() if from_date<=k[2][:10]<=till_date}
            candles["M15"]+=_restore_m15(candles["M15"],restore_groups)
            for tf in FRAMES:
                candles[tf],_= _ordered(candles[tf])
                _validate_candles(candles[tf],tf)
            targets,index=_changed_source_targets(candles,old_features)
            # The checkpoint records actual source rows, not trust in caller-supplied hashes.
            fingerprint=sha({tf:table for tf,table in candles.items()})
            minute_days={}
            for (sec,t),row in sorted(minutes.items()):
                minute_days.setdefault(sec+"|"+t[:10],[]).append(row)
            minute_days={k:sha(v) for k,v in minute_days.items()}
            checkpoint=Path(self.audit_dir)/"latest_source.json" if self.audit_dir else None
            if checkpoint and checkpoint.exists():
                previous=json.loads(checkpoint.read_text(encoding="utf-8"))
                if previous.get("version")!=VERSION:raise MirrorConflict("feature version changed; explicit migration required")
                changed_days={k for k,v in minute_days.items() if previous.get("minute_days",{}).get(k)!=v}
                for tf,table in candles.items():
                    earliest={}
                    if tf in ("H1","M15"):
                        for r in table[1:]:
                            if r[8]+"|"+clock(r[4])[:10] in changed_days:targets.add(r[0])
                    for r in table[1:]:
                        if previous.get("rows",{}).get(r[0])!=sha(r):
                            sec=r[8];t=_stamp(r[4]);earliest[sec]=min(t,earliest.get(sec,t))
                    for r in table[1:]:
                        if r[8] in earliest and _stamp(r[4])>=earliest[r[8]]:targets.add(r[0])
            for tf,table in candles.items():
                if tf in ("H1","M15"):
                    for r in table[1:]:
                        prior=index.get(r[0])
                        if prior and len(prior)>98 and prior[98]=="UNAVAILABLE" and (r[8],tf,clock(r[4])) in groups:
                            targets.add(r[0])
            # Full prior state is needed for ATR/waves and prefix-safe cumulative approximations.
            needed_days={(r[8],tf,clock(r[4])[:10]) for tf,table in candles.items() for r in table[1:] if r[0] in targets}
            minute_targets={k for k in groups if (k[0],k[1],k[2][:10]) in needed_days}
            minute_data=profiles(groups,minute_targets) if targets else {}
            generated=research_features(candles,minute_data,targets=targets)
            plans={};changes=0;repairs=0
            ignore_compare={"calculated_at","source_window_sha256","source_record_sha256","atr_source_hashes_json"}
            for tf in FRAMES:
                old=old_features[tf];desired=_rect([COLUMNS]+[r+[""]*(109-len(r)) for r in old[1:]],109)
                source_index={r[6]:i for i,r in enumerate(desired[1:],1)}
                for row in generated[tf]:
                    if row[6] not in source_index:
                        source_index[row[6]]=len(desired);desired.append(row);continue
                    at=source_index[row[6]];prior=desired[at]
                    for j,new in enumerate(row):
                        if COLUMNS[j] in ignore_compare or new=="":continue
                        if prior[j] in ("","UNAVAILABLE","WARMUP20","WARMUP_OR_SOURCE_INCOMPLETE"):prior[j]=new;changes+=1
                        elif not _same(prior[j],new):
                            if not repair_dependent:raise MirrorConflict("dependent feature conflict; preview and explicit repair_dependent required")
                            if prior[12]!="P3_OHLCV_RESEARCH_V1" or prior[104] not in ("",VERSION):raise MirrorConflict("foreign formula must not be overwritten")
                            prior[j]=new;changes+=1;repairs+=1
                desired,_=_ordered(desired,True);plans[tf+"_INDICATORS"]=desired
                plans[tf]=candles[tf]
            # Preview all tables, schema and payload bounds before any mutation.
            additions={};reorder={};patch_count={}
            for sheet,after in plans.items():
                old=before[sheet];width=len(after[0])
                expanded=[r+[""]*(width-len(r)) for r in old]
                ids={r[0] for r in expanded[1:]}
                additions[sheet]=sum(r[0] not in ids for r in after[1:])
                _,permutation=_ordered(expanded,sheet.endswith("_INDICATORS"))
                reorder[sheet]=permutation!=list(range(len(permutation)))
                patch_count[sheet]=sum(1 for i,x in enumerate(after[0]) if i>=len(old[0]) or old[0][i]!=x)
            summary=dict(version=VERSION,source_sha=self.source_sha,source=source_report,minute_source=minute_status,
                planned_additions=additions,existing_order_repair=reorder,calculated_rows=sum(map(len,generated.values())),filled_cells=changes,repaired_cells=repairs,
                forecast_admission="NOT_GRANTED",historical_availability="UNKNOWN",calendar="UNVERIFIED",price_adjustment="NONE")
            if dry_run:return dict(status="MODEL3_ARCHIVE_DRY_RUN_READY",written=False,**summary)
            if not self.audit_dir:raise MirrorConflict("local audit/backup directory required before writes")
            audit=Path(self.audit_dir);audit.mkdir(parents=True,exist_ok=True)
            run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"_"+uuid.uuid4().hex[:12]
            backup=audit/(run_id+"_before.json")
            backup.write_text(json.dumps(dict(version=VERSION,before=before,plan=summary),ensure_ascii=False,allow_nan=False),encoding="utf-8")
            # Check all snapshots again before even the IN_PROGRESS marker.
            for sheet,data in before.items():
                if digest(self.transport.read_rows("RI",sheet))!=digest(data):raise MirrorConflict("destination changed since preflight")
            mutated=True
            self.transport.update_status("RI",{"model3_status":"IN_PROGRESS","model3_source_sha":self.source_sha})
            for sheet,after in plans.items():
                self._publish(sheet,before[sheet],after)
            verified={}
            for sheet,after in plans.items():
                actual=_rect(self.transport.read_rows("RI",sheet),len(after[0]))
                if digest(actual)!=digest(after):raise MirrorConflict("final cross-sheet readback mismatch")
                verified[sheet]=dict(rows=len(after)-1,sha256=digest(actual))
            receipt=dict(status="MODEL3_ARCHIVE_READBACK_PASS",written=True,backup=str(backup),sheets=verified,**summary)
            (audit/(run_id+"_result.json")).write_text(json.dumps(receipt,ensure_ascii=False),encoding="utf-8")
            staged=audit/(run_id+"_source.json")
            staged.write_text(json.dumps(dict(version=VERSION,fingerprint=fingerprint,minute_days=minute_days,rows={r[0]:sha(r) for table in candles.values() for r in table[1:]})),encoding="utf-8")
            staged.replace(checkpoint)
            self.transport.update_status("RI",{"model3_status":"READBACK_PASS_RESEARCH_ONLY","model3_version":VERSION,"model3_run":run_id,"model3_source_sha":self.source_sha,"model3_forecast_admission":"NOT_GRANTED"})
            return receipt
        except Exception:
            if mutated:
                try:self.transport.update_status("RI",{"model3_status":"FAILED_RECONCILE_BEFORE_RETRY"})
                except Exception:pass
            raise
        finally:self.lock.release()

    def _publish(self,sheet,before,after):
        width=len(after[0]);expected=[r+[""]*(width-len(r)) for r in before]
        edits=[(0,j,x) for j,x in enumerate(after[0]) if j>=len(before[0])]
        index={r[0]:i for i,r in enumerate(expected[1:],1)}
        for row in after[1:]:
            if row[0] in index:
                at=index[row[0]]
                for j,new in enumerate(row):
                    if not _same(expected[at][j],new):edits.append((at,j,new))
        for offset in range(0,len(edits),300):
            if digest(self.transport.read_rows("RI",sheet))!=digest([r[:len(before[0])] if i==0 and len(before[0])<width and offset==0 else r for i,r in enumerate(expected)]):
                # Native values omit trailing blanks; rectangular comparison below handles width growth.
                current=self.transport.read_rows("RI",sheet)
                if _rect(current,len(current[0]))!=[r[:len(current[0])] for r in expected]:raise MirrorConflict("changed before cell patch")
            part=edits[offset:offset+300]
            self.transport.model3_write_cells("RI",sheet,part)
            for r,c,v in part:expected[r][c]=v
            actual=self.transport.read_rows("RI",sheet)
            if _rect(actual,len(actual[0]))!=[r[:len(actual[0])] for r in expected]:raise MirrorConflict("cell patch readback mismatch")
        old_ids=set(index)
        additions=[r for r in after[1:] if r[0] not in old_ids]
        # Native encoding bounds borrowed from the existing derived-update PR263 design.
        for offset in range(0,len(additions),100):
            part=additions[offset:offset+100]
            if _rect(self.transport.read_rows("RI",sheet),width)!=expected:raise MirrorConflict("changed before append")
            self.transport.append_rows("RI",sheet,part);expected+=part
            if _rect(self.transport.read_rows("RI",sheet),width)!=expected:raise MirrorConflict("append readback mismatch; reconcile before retry")
        ordered,permutation=_ordered(expected,sheet.endswith("_INDICATORS"))
        if ordered!=after:raise MirrorConflict("planned content/order mismatch")
        if permutation!=list(range(len(permutation))):
            self.transport.model3_order_rows("RI",sheet,permutation)
            if _rect(self.transport.read_rows("RI",sheet),width)!=after:raise MirrorConflict("row ordering readback mismatch")
