"""Google-first source collection and shared, research-only RI roll coordinates.

One orchestration lock; existing collector, indicators, selector and coordinate
engine own their respective algorithms. No second price/volume algorithm.
"""
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import threading
import uuid

from birzha.application.archive_collection import ArchiveCollector
from birzha.application.canonical_market_mirror import MirrorConflict, digest, rectangular
from birzha.application.continuous_series import build_continuous_series, VERSION
from birzha.application.continuous_archive_schema import CONTINUOUS_COLUMNS
from birzha.application.forts_calendar_2026 import trade_day, primary_days, kind, VERSION as CALENDAR
from birzha.application.home_market_mirror import _stamp
from birzha.application.instrument_archive_registry import archive_contract
from birzha.application.model3_archive import FRAMES, _validate_candles, _rect, _same
from birzha.application.model3_features import COLUMNS, packed, sha
from birzha.providers.continuous_futures import select_completed_session_volume


@dataclass
class ArchiveSync:
    transport: object
    market: object
    updater: object
    audit_dir: str | None
    lock: object

    def sync(self, *, symbol, from_date, till_date, dry_run=True,
             exclusive_workbook_owner=False, repair_dependent=False,
             max_source_windows=6):
        contract=archive_contract(symbol)
        if contract['symbol']!='RI':
            raise MirrorConflict('automatic continuous archive policy is currently RI only')
        left,right=date.fromisoformat(from_date),date.fromisoformat(till_date)
        now=datetime.now(timezone.utc)
        if not 0<=(right-left).days<367 or left.year!=2026 or right.year!=2026:
            raise MirrorConflict('documented automatic calendar only covers bounded 2026 RI dates')
        if right>=now.astimezone(_stamp('2026-01-01').tzinfo).date():
            raise MirrorConflict('only completed civil dates before today are accepted')
        if not isinstance(max_source_windows,int) or isinstance(max_source_windows,bool) or not 1<=max_source_windows<=12:
            raise MirrorConflict('source budget must be 1..12 exact-contract day/timeframe windows')
        if not dry_run and (not exclusive_workbook_owner or not self.audit_dir):
            raise MirrorConflict('sole workbook writer and local backups required')
        if not self.lock.acquire(blocking=False):raise MirrorConflict('archive writer busy')
        changed=False
        try:
            raw=self._raw()
            # Read the destination and all indicator schemas before any source request.
            features={}
            for tf in FRAMES:
                sheet=tf+'_INDICATORS';self.transport.validate_model3_target('RI',sheet)
                features[sheet]=self.transport.read_rows('RI',sheet)
            self.transport.validate_service_target('RI','SYNC_STATUS')
            state=dict(self.transport.read_status('RI')[1:])
            preview=replace(self.updater,lock=threading.Lock()).update(
                from_date=from_date,till_date=till_date,dry_run=True,
                repair_dependent=repair_dependent)
            prior_dates=[_stamp(r[t[0].index('bar_start_time')]).date()
                         for t in raw.values() for r in t[1:]]
            anchor=min([left,*prior_dates])
            anchor=max(anchor,date(2026,1,5))
            if state.get('continuous_anchor'):
                fixed=date.fromisoformat(state['continuous_anchor'])
                if anchor<fixed:raise MirrorConflict('backfill before coordinate anchor requires explicit coordinate migration')
                anchor=fixed
            days=primary_days(anchor,right)
            if not days:raise MirrorConflict('no completed primary trade session in requested window')
            if dry_run:
                return dict(status='ARCHIVE_SYNC_PLANNED',written=False,
                    canonical_storage='GOOGLE_SHEETS',symbol='RI',anchor=anchor.isoformat(),
                    calendar_version=CALENDAR,coordinate='FORWARD_SPREAD',
                    target_trade_sessions=len(days),indicator_plan=preview,
                    steps=['REUSE_GOOGLE_AND_SAVED_M1','OFFICIAL_T_MINUS_1_CANDIDATES',
                           'MISSING_SOURCE_ONLY','SHARED_INDICATORS','CONTINUOUS_COLUMNS_READBACK'],
                    network_requests=0,forecast_allowed=False)
            audit=Path(self.audit_dir);audit.mkdir(parents=True,exist_ok=True)
            backup=audit/(uuid.uuid4().hex+'_continuous_before.json')
            backup.write_text(json.dumps(dict(raw=raw,indicators=features,status=state),
                ensure_ascii=False,allow_nan=False),encoding='utf8')
            if self._raw()!=raw:raise MirrorConflict('source changed during sync preflight')
            changed=True
            self.transport.update_status('RI',{'archive_sync_status':'IN_PROGRESS'})
            # Restore saved sparse/dense M15 before considering any MOEX candle call.
            local_updater=replace(self.updater,lock=threading.Lock())
            local_updater.update(from_date=from_date,till_date=till_date,dry_run=False,
                exclusive_workbook_owner=True,repair_dependent=repair_dependent)
            raw=self._raw()
            sessions=[];selected={};metadata_updates={};previous=None
            for day in days:
                session=self._decision(day,state,audit)
                decision_day=date.fromisoformat(session['decision_session'])
                if previous is not None and decision_day!=previous:
                    raise MirrorConflict('published calendar differs from official observed trade dates')
                decision=select_completed_session_volume('RI',decision_session=decision_day,
                    effective_session=day,decision_rows=session['decision_rows'])
                sessions.append(session);selected[day.isoformat()]=decision.secid
                key='continuous_decision|'+day.isoformat()
                metadata_updates[key]=packed(session)
                previous=day
            # Source evidence is persistent even if a later source window fails.
            self.transport.update_status('RI',metadata_updates)
            civil=[];d=anchor
            while d<=right:
                if d>=left and kind(d)!='CLOSED' and trade_day(d).isoformat() in selected:civil.append(d)
                d+=timedelta(days=1)
            used=0;unproven=[];unavailable=[]
            for day in civil:
                sec=selected[trade_day(day).isoformat()]
                for tf in FRAMES:
                    # D1 uses official TRADEDATE, not the weekend civil label.
                    if tf=='D1' and kind(day)!='PRIMARY':continue
                    table=raw[tf];h=table[0]
                    rows=[r for r in table[1:] if r[h.index('contract_code')]==sec
                          and _stamp(r[h.index('bar_start_time')]).date()==day]
                    if rows:
                        receipt=f'archive_source|{sec}|{tf}|{day}|{day}'
                        if receipt not in state:unproven.append(f'{sec}|{tf}|{day}')
                        elif json.loads(state[receipt]).get('rows_sha256')!=digest(rows):
                            raise MirrorConflict('source receipt no longer matches current Google rows')
                        continue
                    receipt_key=f'archive_source|{sec}|{tf}|{day}|{day}'
                    if receipt_key in state:
                        receipt=json.loads(state[receipt_key])
                        if receipt.get('rows_sha256')!=digest([]):
                            raise MirrorConflict('missing Google rows contradict their source receipt')
                        if receipt.get('source_rows')==0:
                            unavailable.append(f'{sec}|{tf}|{day}');continue
                    if used>=max_source_windows:
                        self.transport.update_status('RI',{'archive_sync_status':'PARTIAL_RESUME_SAME_ARGUMENTS'})
                        return dict(status='PARTIAL_SOURCE_BUDGET',written=True,
                            next_window=dict(secid=sec,timeframe=tf,date=day.isoformat()),
                            source_windows=used,continuous_written=False,
                            reason='repeat same command; prior windows are reused',forecast_allowed=False)
                    result=ArchiveCollector(self.transport,self.market,self.audit_dir,threading.Lock()).collect(
                        symbol='RI',exact_secid=sec,timeframe=tf,from_date=day.isoformat(),
                        till_date=day.isoformat(),dry_run=False,exclusive_workbook_owner=True)
                    used+=1;raw=self._raw()
                    # A genuine empty response is a gap, never a synthetic zero candle.
                    if result.get('source_rows')==0:
                        unavailable.append(f'{sec}|{tf}|{day}')
            local_updater.update(from_date=from_date,till_date=till_date,dry_run=False,
                exclusive_workbook_owner=True,repair_dependent=repair_dependent)
            if unavailable:
                self.transport.update_status('RI',{'archive_sync_status':'PARTIAL_SOURCE_UNAVAILABLE',
                    'continuous_missing_source':packed(unavailable)})
                return dict(status='PARTIAL_SOURCE_UNAVAILABLE',written=True,
                    unavailable=unavailable,source_windows=used,continuous_written=False,
                    reason='empty source responses preserved; no synthetic zero bars',forecast_allowed=False)
            raw=self._raw();bars=[]
            for tf,table in raw.items():
                h=table[0]
                for row in table[1:]:
                    r=dict(zip(h,row));start=_stamp(r['bar_start_time']);civil_day=start.date()
                    if civil_day<left or civil_day>right:continue
                    mapped=trade_day(civil_day).isoformat()
                    if mapped not in selected:continue
                    if tf=='D1' and kind(civil_day)!='PRIMARY':
                        raise MirrorConflict('D1 must carry the official primary trade date')
                    # Nominal interval elapsed is distinct from source density/legacy flag.
                    end=start+timedelta(minutes=15 if tf=='M15' else 60) if tf!='D1' else _stamp(str(civil_day+timedelta(days=1)))
                    bars.append(dict(secid=r['contract_code'],timeframe=tf,trade_session_date=mapped,
                        begin=start.isoformat(),end=end.isoformat(),source_end=r['bar_end_time'],
                        source_complete_flag=r.get('is_complete'),completed=end<=now,
                        **{k:r[k] for k in ('open','high','low','close','volume')},
                        source_record_key=r['record_key'],source_sha256=sha(row)))
            payload=dict(root='RI',coordinate='FORWARD_SPREAD',evidence_mode='RECONSTRUCTED_RESEARCH',
                as_of=now.isoformat(),sessions=sessions,bars=bars,
                required_sessions=[d.isoformat() for d in days if d>=left])
            result=build_continuous_series(payload)
            output={b['source_record_key']:b for b in result['bars']}
            prefix_by_day={};prefix=''
            for session in sessions:
                prefix=sha([prefix,session]);prefix_by_day[session['effective_session']]=prefix
            basis={e['effective_session']:e['basis'] for e in result['roll_events']}
            reports={}
            # Reuse the established backup/CAS/patch/readback writer, preserving 109 fields.
            for tf in FRAMES:
                sheet=tf+'_INDICATORS';before=self.transport.read_rows('RI',sheet)
                if before[0]!=COLUMNS:raise MirrorConflict('shared indicator migration incomplete')
                desired=_rect(deepcopy(before),len(COLUMNS));seen=set()
                for row in desired[1:]:
                    key=row[COLUMNS.index('source_record_key')]
                    begin=_stamp(row[COLUMNS.index('begin')])
                    if not left<=begin.date()<=right:continue
                    mapped=trade_day(begin.date()).isoformat()
                    if mapped not in selected:continue
                    bar=output.get(key);seen.add(key)
                    values=[VERSION,'FORWARD_SPREAD',bar is not None,
                        *([bar[k] for k in ('open','high','low','close')] if bar else ['']*4),
                        sha(dict(bar=bar,ledger_sha256=prefix_by_day[mapped],calendar=CALENDAR)) if bar else '',mapped,basis.get(mapped,0)]
                    for field,expected in [('continuous_version',VERSION),('continuous_coordinate','FORWARD_SPREAD')]:
                        if row[COLUMNS.index(field)] not in ('',expected):
                            raise MirrorConflict('existing coordinate/version requires explicit migration')
                    for column,value in zip(CONTINUOUS_COLUMNS,values):
                        row[COLUMNS.index(column)]=value
                required={k for k,b in output.items() if b['timeframe']==tf and left<=_stamp(b['begin']).date()<=right}
                if not required<=seen:raise MirrorConflict('selected source has no matching indicator row')
                local_updater._publish(sheet,before,desired)
                actual=self.transport.read_rows('RI',sheet)
                if _rect(actual,len(COLUMNS))!=desired:raise MirrorConflict('continuous readback mismatch')
                reports[sheet]=dict(rows=len(desired)-1,sha256=digest(desired))
            if self._raw()!=raw:raise MirrorConflict('RAW changed during coordinate publication')
            status={'archive_sync_status':'CONTINUOUS_READBACK_RESEARCH_ONLY',
                'continuous_anchor':anchor.isoformat(),'continuous_calendar':CALENDAR,
                'continuous_coordinate':'FORWARD_SPREAD','continuous_version':VERSION,
                'continuous_roll_events':packed(result['roll_events']),
                'continuous_first_available':'UNKNOWN','continuous_forecast_allowed':'FALSE'}
            self.transport.update_status('RI',status)
            if any(dict(self.transport.read_status('RI')[1:]).get(k)!=v for k,v in status.items()):
                raise MirrorConflict('continuous ledger status readback mismatch')
            receipt=dict(status='ARCHIVE_SYNC_READBACK_RESEARCH_ONLY',written=True,backup=str(backup),
                sheets=reports,selected_bars=len(output),roll_events=result['roll_events'],
                raw_modified=False,coordinate='FORWARD_SPREAD',calendar_version=CALENDAR,
                source_windows=used,unattested_existing_windows=len(unproven),
                full_source_coverage='NOT_PROVEN' if unproven else 'PROVIDER_RESPONSES_ONLY',
                historical_first_available='UNKNOWN',forecast_allowed=False)
            (audit/(uuid.uuid4().hex+'_continuous_result.json')).write_text(
                json.dumps(receipt,ensure_ascii=False,allow_nan=False),encoding='utf8')
            return receipt
        except Exception:
            if changed:
                try:self.transport.update_status('RI',{'archive_sync_status':'FAILED_RECONCILE_BEFORE_RETRY'})
                except Exception:pass
            raise
        finally:self.lock.release()

    def _raw(self):
        result={}
        for tf in FRAMES:
            self.transport.validate_model3_target('RI',tf)
            result[tf]=rectangular(self.transport.read_rows('RI',tf))
            _validate_candles(result[tf],tf)
        return result

    def _decision(self,day,state,audit):
        key='continuous_decision|'+day.isoformat()
        cached=state.get(key)
        path=audit/('decision_'+day.isoformat()+'.json')
        if cached is None and path.exists():cached=path.read_text(encoding='utf8')
        if cached is not None:
            value=json.loads(cached)
            if value.get('calendar_version')!=CALENDAR or value.get('effective_session')!=day.isoformat():
                raise MirrorConflict('decision cache version/session mismatch')
            if value.get('evidence_sha256')!=sha(value['decision_rows']):
                raise MirrorConflict('decision evidence cache hash mismatch')
            return value
        resolver=self.market.historical_future_resolver
        prior,rows=resolver._previous_observed_session('RI',day)
        # The decision occurs before ANY civil-day segment of the target session.
        first=day
        while first>date(2026,1,5):
            preceding=first-timedelta(days=1)
            if kind(preceding)=='CLOSED' or trade_day(preceding)!=day:break
            first=preceding
        normalized=[]
        for source in rows:
            row=dict(source)
            row.update(completed_at=prior.isoformat()+'T23:59:59+03:00',
                       available_at=None,source_sha256=sha(source))
            normalized.append(row)
        value=dict(effective_session=day.isoformat(),decision_session=prior.isoformat(),
            decision_cutoff=first.isoformat()+'T00:00:00+03:00',decision_rows=normalized,
            calendar_version=CALENDAR,evidence_sha256=sha(normalized),
            historical_first_available='UNKNOWN')
        if len(packed(value))>45000:raise MirrorConflict('decision evidence exceeds Google cell bound')
        staged=path.with_suffix('.tmp');staged.write_text(packed(value),encoding='utf8');staged.replace(path)
        return value
