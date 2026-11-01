"""Google canonical archive orchestration. No filesystem, DuckDB or local state."""
from dataclasses import asdict,dataclass,field
from datetime import date,datetime,timedelta,timezone
import hashlib,json,threading,uuid,re
from zoneinfo import ZoneInfo
from birzha.application.canonical_market_mirror import MirrorConflict,CanonicalMirrorPublisher,rectangular,digest,index_rows,sheet_timestamp,validate_identity
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge
from birzha.domain.market import Instrument,Candle
from birzha.providers.moex_iss import MoexIssClient
from birzha.storage.google_m1_bytes import validate_zip

SYMBOLS=('RI','Si','BR','GOLD','SBER','IMOEX')
MSK=ZoneInfo('Europe/Moscow')
ARCHIVE_START_DATE=date(2025,1,1)
def stamp(v):
    d=sheet_timestamp(v);return d.replace(tzinfo=MSK) if d.tzinfo is None else d.astimezone(MSK)
def packed(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def identity(symbol,secid,tf):
    validate_identity([['record_key','bar_start_time','contract_code'],[f'{symbol}|{tf}|{secid}|2000-01-01','2000-01-01',secid]],symbol,tf,incoming=False)

@dataclass
class GoogleFirstArchive:
    transport: object
    market: object
    source_sha: str
    sole_writer: bool=False
    lock: object=field(default_factory=threading.Lock)
    now: object=field(default=lambda:datetime.now(timezone.utc))

    def _schemas(self,symbol,tf):
        self.transport.validate_target(symbol,tf)
        rows=rectangular(self.transport.read_rows(symbol,tf));index_rows(rows);validate_identity(rows,symbol,tf,incoming=False)
        h=rows[0];keys=[(r[h.index('contract_code')],stamp(r[h.index('bar_start_time')])) for r in rows[1:]]
        if len(keys)!=len(set(keys)):raise MirrorConflict('SEMANTIC_DUPLICATE_KEYS')
        self.transport.validate_service_target(symbol,'SYNC_STATUS')
        self.transport.validate_service_target(symbol,'QUALITY_AUDIT')
        self.transport.validate_service_target(symbol,'CONTRACTS')
        return rows

    def _status(self,symbol):
        # A restarted process has no cached Google tab identity. Always validate
        # the pinned service target before the native transport reads its rows.
        self.transport.validate_service_target(symbol,'SYNC_STATUS')
        return dict(self.transport.read_status(symbol)[1:])

    def _audit(self,symbol,run,scope,status,count=0):
        # Fixed codes only; upstream exception text, arguments and credentials never enter audit.
        self.transport.append_quality(symbol,{'audit_id':run+'|'+status,'scope':scope,'status':status,
            'description':'Google-first archive; raw OHLCV retained','checked_at':self.now().isoformat(),
            'affected_rows':count,'limitation':'SESSION_AND_PIT_UNVERIFIED','owner':'MCP',
            'source_sheet':scope,'evidence_ref':self.source_sha})

    def collect(self,*,symbol,timeframe,day,exact_secid=None,dry_run=True):
        symbol=GoogleSheetsBridge.target_for_symbol(symbol)[0]
        if timeframe not in ('D1','H1','M15'):raise MirrorConflict('TIMEFRAME_UNSUPPORTED')
        target=date.fromisoformat(day)
        if day!=target.isoformat() or target>=self.now().astimezone(MSK).date():raise MirrorConflict('COMPLETED_CIVIL_DAY_REQUIRED')
        if type(dry_run) is not bool:raise MirrorConflict('BOOLEAN_DRY_RUN_REQUIRED')
        if not dry_run and not self.sole_writer:raise MirrorConflict('SOLE_WRITER_REQUIRED')
        if not self.lock.acquire(blocking=False):raise MirrorConflict('ARCHIVE_BUSY')
        run=uuid.uuid4().hex;started=False
        try:
            old=self._schemas(symbol,timeframe);h=old[0];state=self._status(symbol)
            key=f'google_first|{symbol}|{timeframe}|{day}'
            receipt=json.loads(state[key]) if state.get(key) else None
            if receipt and exact_secid is not None and receipt.get('secid')!=exact_secid:raise MirrorConflict('EXACT_CONTRACT_NOT_T_MINUS_1_SELECTION')
            if receipt and receipt.get('phase')=='COMMITTED':
                secid=receipt['secid'];identity(symbol,secid,timeframe)
                selected=[r for r in old[1:] if r[h.index('contract_code')]==secid and stamp(r[h.index('bar_start_time')]).date()==target]
                if digest(selected)!=receipt['rows_sha256']:raise MirrorConflict('RECEIPT_CONFLICT')
                if not dry_run:
                    self.transport.update_status(symbol,{'google_first_status':'REUSED'})
                    self._audit(symbol,run,timeframe,'REUSED')
                return {'status':'REUSED','written':False,'source_requests':0,'secid':secid,'rows':len(selected)}
            if receipt and receipt.get('phase')=='EMPTY' and self.now()-datetime.fromisoformat(receipt['observed_at'])<timedelta(hours=1):
                return {'status':'SOURCE_NOT_AVAILABLE_RETRY_LATER','written':False,'source_requests':0}
            if dry_run:
                return {'status':'PLANNED','written':False,'source_requests':0,'symbol':symbol,'timeframe':timeframe,'day':day,'existing_rows':sum(stamp(r[h.index('bar_start_time')]).date()==target for r in old[1:])}
            started=True
            self.transport.update_status(symbol,{'google_first_status':'RUNNING','google_first_run':run,'primary_store':'GOOGLE_SHEETS'})
            self._audit(symbol,run,timeframe,'RUNNING')
            # Persist and reuse the T-1 exact-contract decision; never substitute index RTSI.
            decision_key=f'google_first_contract|{day}'
            if state.get(decision_key):
                instrument=Instrument(**json.loads(state[decision_key]))
            else:
                instrument=self.market.resolve({'SI':'Si'}.get(symbol,symbol),as_of=target)
                identity(symbol,instrument.secid,timeframe)
                if symbol in ('RI','SI','BR','GOLD') and (instrument.engine,instrument.market)!=('futures','forts'):raise MirrorConflict('FUTURES_ROUTE_REQUIRED')
                self.transport.update_status(symbol,{decision_key:packed(asdict(instrument))})
            if symbol in ('RI','SI','BR','GOLD') and (instrument.engine,instrument.market)!=('futures','forts'):raise MirrorConflict('FUTURES_ROUTE_REQUIRED')
            if exact_secid is not None and instrument.secid!=exact_secid:raise MirrorConflict('EXACT_CONTRACT_NOT_T_MINUS_1_SELECTION')
            secid=instrument.secid;identity(symbol,secid,timeframe)
            existing=[r for r in old[1:] if r[h.index('contract_code')]==secid and stamp(r[h.index('bar_start_time')]).date()==target]

            # A day receipt is committed only after the bounded full response was read back.
            # Never re-download a populated unproven intraday window.
            if existing and timeframe!='M15' and not (receipt and receipt.get('phase')=='PREPARED'):
                self._audit(symbol,run,timeframe,'EXISTING_UNVERIFIED_NO_FETCH')
                self.transport.update_status(symbol,{'google_first_status':'EXISTING_UNVERIFIED_NO_FETCH'})
                return {'status':'EXISTING_UNVERIFIED_NO_FETCH','written':False,'source_requests':0,'secid':secid}
            rows=None;source_requests=0;archive_ref=''
            if timeframe=='M15':
                # Verify folder/index before spending any minute requests.
                self.transport.validate_m1(symbol,secid,target.strftime('%Y-%m'))
                cached=self.transport.read_m1(symbol,secid,target.strftime('%Y-%m'))
                if cached:
                    minutes=[r for r in validate_zip(cached['body'],symbol,secid,target.strftime('%Y-%m'),cached['sha256']) if stamp(r['begin']).date()==target]
                    if minutes:
                        rows=tuple(Candle(**{k:r.get(k) for k in ('open','high','low','close','volume','value','begin','end')}) for r in minutes)
                        archive_ref=cached['file_id']
                if rows is None and existing and not (receipt and receipt.get('phase')=='PREPARED'):
                    self._audit(symbol,run,timeframe,'EXISTING_UNVERIFIED_NO_FETCH')
                    return {'status':'EXISTING_UNVERIFIED_NO_FETCH','written':False,'source_requests':0}
            # A crash/timeout after FETCHING never triggers blind source retries.
            # Operator can reconcile the cloud M1 ZIP or a prepared cloud candle receipt.
            if receipt and receipt.get('phase')=='PREPARED':
                if receipt.get('header')!=h or type(receipt.get('chunks')) is not int or not 0<=receipt['chunks']<=128:raise MirrorConflict('PREPARED_SCHEMA_CONFLICT')
                incoming=[receipt['header']]
                for i in range(receipt['chunks']):incoming.extend(json.loads(state[f'{key}|prepared|{i}']))
                if digest(incoming)!=receipt['incoming_sha256']:raise MirrorConflict('PREPARED_RECEIPT_CONFLICT')
                source_requests=0
            else:
                if rows is None:
                    if receipt and receipt.get('phase')=='FETCHING':raise MirrorConflict('SOURCE_RESULT_UNKNOWN_REVIEW_REQUIRED')
                    self.transport.update_status(symbol,{key:packed({'phase':'FETCHING','secid':secid,'run':run})})
                    series=self.market.candles_for_instrument(instrument,timeframe='M1' if timeframe=='M15' else timeframe,from_date=day,till_date=day,completed_only=False)
                    if series.instrument.secid!=secid or series.timeframe!=('M1' if timeframe=='M15' else timeframe):raise MirrorConflict('SOURCE_IDENTITY_MISMATCH')
                    rows=series.candles;source_requests=1
                    for candle in rows:
                        begin,end=stamp(candle.begin),stamp(candle.end)
                        if begin.date()!=target or end<begin or end>=self.now().astimezone(MSK) or not candle.completed:raise MirrorConflict('SOURCE_TIME_OUTSIDE_COMPLETED_DAY')
                    if timeframe=='M15' and rows:
                        archive_ref=self.transport.publish_m1(symbol,secid,target.strftime('%Y-%m'),[asdict(c) for c in rows])['file_id']
                candles=MoexIssClient._aggregate_m15(rows,as_of=self.now()) if timeframe=='M15' else rows
                incoming=self._project(symbol,secid,timeframe,target,candles,h,archive_ref)
                if digest(rectangular(self.transport.read_rows(symbol,timeframe)))!=digest(old):raise MirrorConflict('CONCURRENT_CHANGE')
                # Bounded one-day payload retained in existing Google SYNC_STATUS, no local WAL.
                chunks=[incoming[1:][i:i+16] for i in range(0,len(incoming)-1,16)]
                prep={'phase':'PREPARED','secid':secid,'header':h,'chunks':len(chunks),'incoming_sha256':digest(incoming),'run':run}
                updates={f'{key}|prepared|{i}':packed(chunk) for i,chunk in enumerate(chunks)}
                if any(len(v)>45000 for v in updates.values()):raise MirrorConflict('CLOUD_RECEIPT_CELL_LIMIT')
                self.transport.update_status(symbol,{**updates,key:packed(prep)})

            # Existing matching semantic rows preserve original key/provenance exactly.
            current=rectangular(self.transport.read_rows(symbol,timeframe))
            known={(r[h.index('contract_code')],stamp(r[h.index('bar_start_time')])):r for r in current[1:]}
            merged=[h]
            for r in incoming[1:]:
                prior=known.get((r[h.index('contract_code')],stamp(r[h.index('bar_start_time')])))
                if prior is not None:
                    if any(prior[h.index(k)]!=r[h.index(k)] for k in ('open','high','low','close','volume')):raise MirrorConflict('SOURCE_PRICE_CONFLICT')
                    continue # existing numeric/legacy timestamps are never republished as new rows
                merged.append(r)
            result=CanonicalMirrorPublisher(self.transport).sync(symbol=symbol,timeframe=timeframe,incoming=merged,exclusive_workbook_owner=True)
            after=rectangular(self.transport.read_rows(symbol,timeframe))
            selected=[r for r in after[1:] if r[h.index('contract_code')]==secid and stamp(r[h.index('bar_start_time')]).date()==target]
            if len(incoming)==1:
                self.transport.update_status(symbol,{key:packed({'phase':'EMPTY','secid':secid,'observed_at':self.now().isoformat()}),'google_first_status':'SOURCE_NOT_AVAILABLE'})
                self._audit(symbol,run,timeframe,'SOURCE_NOT_AVAILABLE')
                return {'status':'SOURCE_NOT_AVAILABLE','written':False,'source_requests':source_requests,'secid':secid}
            final={'phase':'COMMITTED','secid':secid,'rows_sha256':digest(selected),'source_rows':len(incoming)-1,'calendar':'UNVERIFIED','historical_first_seen':'UNKNOWN'}
            self.transport.update_status(symbol,{**{f'{key}|prepared|{i}':'' for i in range((len(incoming)-1+15)//16)},key:packed(final),'google_first_status':'READBACK_PASS'})
            self._audit(symbol,run,timeframe,'READBACK_PASS',result['added_rows'])
            return {**result,'status':'READBACK_PASS','written':True,'secid':secid,'source_requests':source_requests,'canonical_storage':'GOOGLE_SHEETS','indicators':'EXISTING_FORMULAS_PRESERVED_NOT_RECOMPUTED'}
        except Exception as exc:
            code=str(exc) if isinstance(exc,MirrorConflict) and re.fullmatch(r'[A-Z0-9_]{1,96}',str(exc)) else 'UPSTREAM_OR_RUNTIME_ERROR'
            if started:
                try:
                    self.transport.update_status(symbol,{'google_first_status':'ERROR_RECONCILE_BEFORE_RETRY','google_first_error_code':code})
                    self._audit(symbol,run,timeframe,'ERROR_RECONCILE_BEFORE_RETRY')
                except Exception:pass
            raise
        finally:self.lock.release()

    def _project(self,symbol,secid,tf,target,candles,header,source_ref):
        output=[header]
        for c in candles:
            begin=stamp(c.begin);end=stamp(c.end)
            if begin.date()!=target or end>=self.now().astimezone(MSK) or end<begin:raise MirrorConflict('SOURCE_TIME_OUTSIDE_COMPLETED_DAY')
            if not c.completed:raise MirrorConflict('UNFINISHED_SOURCE_BAR')
            values={'record_key':f'{symbol}|{tf}|{secid}|{begin.isoformat()}','calendar_date':target.isoformat(),
                'trade_session_date':'','session_id':'','bar_start_time':begin.isoformat(),'bar_end_time':end.isoformat(),
                'available_at':'','available_at_confidence':'UNKNOWN','contract_code':secid,'open':c.open,'high':c.high,'low':c.low,'close':c.close,
                'volume':'' if c.volume is None and symbol=='IMOEX' else c.volume,'number_of_trades':'','turnover':'' if c.value is None else c.value,
                'is_complete':c.completed and (tf!='M15' or c.observed_m1_count==15) and (tf!='H1' or (end-begin).total_seconds() in (3599,3600)), 'source_interval':tf,'source_candles':c.observed_m1_count or '',
                'source_id':'MOEX_ISS','research_status':'RESEARCH_ONLY_GOOGLE_FIRST','normalized_sha256':'','source_archive_sha256':'',
                'capture_day_status':c.observation_quality or 'SOURCE_INTERVAL_CLOSED_NOT_CALENDAR_ATTESTED','source_kind':'GOOGLE_FIRST_MOEX',
                'caveat':'SESSION_AND_PIT_UNVERIFIED','source_capture_label':'','source_file_id':source_ref}
            if set(header)-set(values):raise MirrorConflict('UNSUPPORTED_CANDLE_SCHEMA')
            output.append([values[k] for k in header])
        index_rows(output);validate_identity(output,symbol,tf,incoming=True)
        return output

    def _scan_cursor(self,symbol,tf,state=None):
        """Cloud-only, per-instrument/per-timeframe full-horizon cursor.

        A cursor beyond yesterday means the previous bounded sweep finished.
        The next sweep starts again at 2025-01-01, so older unresolved gaps
        cannot be hidden by a fresh last_date.
        """
        if state is None:state=self._status(symbol)
        key=f'google_first_scan_cursor|{tf}'
        raw=state.get(key,ARCHIVE_START_DATE.isoformat())
        try:
            cursor=date.fromisoformat(raw)
            if raw!=cursor.isoformat():raise ValueError('not canonical')
        except (ValueError,TypeError):raise MirrorConflict('SCAN_CURSOR_INVALID') from None
        if cursor<ARCHIVE_START_DATE:raise MirrorConflict('SCAN_CURSOR_BEFORE_POLICY_START')
        yesterday=self.now().astimezone(MSK).date()-timedelta(days=1)
        return ARCHIVE_START_DATE if cursor>yesterday else cursor

    def sync(self,*,symbol,from_date,till_date,timeframes=('D1','H1','M15'),
             dry_run=True,max_source_windows=6,scan_mode:bool=False):
        """Collect up to three days; optionally persist safe sweep progress in Google.

        The automatic scheduler uses one timeframe at a time with scan_mode;
        explicitly requested backfills do not advance its shared cursor.
        """
        left,right=date.fromisoformat(from_date),date.fromisoformat(till_date)
        if not 0<=(right-left).days<3 or not timeframes or len(set(timeframes))!=len(timeframes):raise MirrorConflict('BOUNDED_THREE_DAY_RANGE_REQUIRED')
        if type(max_source_windows) is not int or not 1<=max_source_windows<=12:raise MirrorConflict('INVALID_SOURCE_BUDGET')
        if type(scan_mode) is not bool:raise MirrorConflict('BOOLEAN_SCAN_MODE_REQUIRED')
        if scan_mode and len(timeframes)!=1:raise MirrorConflict('SCAN_ONE_TIMEFRAME_REQUIRED')
        symbol=GoogleSheetsBridge.target_for_symbol(symbol)[0]
        tf=timeframes[0] if scan_mode else None
        cursor_key=f'google_first_scan_cursor|{tf}' if scan_mode else None
        if scan_mode:
            state=self._status(symbol)
            expected=self._scan_cursor(symbol,tf,state)
            if left!=expected:raise MirrorConflict('SCAN_CURSOR_MISMATCH')
            if right>self.now().astimezone(MSK).date()-timedelta(days=1):raise MirrorConflict('SCAN_FUTURE_DAY')
            if not dry_run and not self.sole_writer:raise MirrorConflict('SOLE_WRITER_REQUIRED')
        results=[];day=left
        safe=('READBACK_PASS','REUSED','SOURCE_NOT_AVAILABLE',
              'SOURCE_NOT_AVAILABLE_RETRY_LATER','EXISTING_UNVERIFIED_NO_FETCH')
        while day<=right:
            for timeframe in timeframes:
                if len(results)>=max_source_windows:return {
                    'status':'PARTIAL','results':results,'next_day':day.isoformat(),'next_timeframe':timeframe}
                result=self.collect(symbol=symbol,timeframe=timeframe,day=day.isoformat(),dry_run=dry_run)
                results.append(result)
                if scan_mode and not dry_run:
                    if result['status'] not in safe:
                        # Never move the cursor past an unknown outcome, incomplete
                        # budget, or any result not explicitly classified as safe.
                        return {'status':'PARTIAL','results':results,
                                'next_day':day.isoformat(),'next_timeframe':timeframe}
                    next_cursor=(day+timedelta(days=1)).isoformat()
                    self.transport.update_status(symbol,{
                        cursor_key:next_cursor,
                        f'google_first_scan_updated_at|{tf}':self.now().isoformat()})
                    if dict(self.transport.read_status(symbol)[1:]).get(cursor_key)!=next_cursor:
                        raise MirrorConflict('SCAN_CURSOR_READBACK_FAILED')
            day+=timedelta(days=1)
        return {'status':'PLANNED' if dry_run else
                ('PASS' if all(r['status'] in ('REUSED','READBACK_PASS') for r in results) else 'PARTIAL'),
                'results':results,'written':any(r.get('written') for r in results),
                'scan_cursor':(right+timedelta(days=1)).isoformat() if scan_mode and not dry_run else None}

    def model3_coverage(self,*,timeframe='D1'):
        """Read-only RI candle/indicator key coverage, not a formula acceptance."""
        if timeframe not in ('D1','H1','M15'):raise MirrorConflict('TIMEFRAME_UNSUPPORTED')
        from birzha.application.model3_indicator_coverage import coverage_summary
        candles=self._schemas('RI',timeframe)
        indicator_sheet=timeframe+'_INDICATORS'
        self.transport.validate_model3_target('RI',indicator_sheet)
        # Indicator sheets legitimately have 95 columns; the candle-only
        # rectangular() validator caps widths at 40. Coverage validates the
        # existing exact header and pads rows without changing Google.
        indicators=self.transport.read_rows('RI',indicator_sheet)
        return coverage_summary(candles,indicators,timeframe=timeframe)

    def model3_backfill(self,*,timeframe,from_date,till_date,max_rows=50,
                        dry_run=True,owner_confirmed=False,
                        include_minute_proxies=False):
        """RI-only cloud-native research backfill, without local history files.

        Only one 50-row *chronological gap* may be inserted per call. The
        Google batch is atomic. A lost response remains an unknown outcome:
        the operator must re-read coverage before manually retrying.
        """
        if timeframe not in ('D1','H1','M15'):raise MirrorConflict('MODEL3_TIMEFRAME_UNSUPPORTED')
        if (type(dry_run) is not bool or type(owner_confirmed) is not bool
                or type(include_minute_proxies) is not bool):
            raise MirrorConflict('MODEL3_EXPLICIT_BOOLEANS_REQUIRED')
        if not dry_run and (not self.sole_writer or not owner_confirmed):
            raise MirrorConflict('MODEL3_OWNER_AND_SOLE_WRITER_REQUIRED')
        from_date_parsed=date.fromisoformat(from_date)
        till_date_parsed=date.fromisoformat(till_date)
        if till_date_parsed>=self.now().astimezone(MSK).date():
            raise MirrorConflict('MODEL3_COMPLETED_DAYS_ONLY')
        if not self.lock.acquire(blocking=False):raise MirrorConflict('ARCHIVE_BUSY')
        try:
            from birzha.application.model3_google_backfill import plan_ri_indicator_backfill
            candles=self._schemas('RI',timeframe)
            daily=candles if timeframe=='D1' else self._schemas('RI','D1')
            sheet=timeframe+'_INDICATORS'
            self.transport.validate_model3_target('RI',sheet)
            old=self.transport.read_rows('RI',sheet)
            if not old or any(len(row)>len(old[0]) for row in old):
                raise MirrorConflict('MODEL3_INDICATOR_ROWS_INVALID')
            # Do not shift user-owned formulas/references. This path is for
            # existing frozen research literals only.
            if any(isinstance(v,str) and v.startswith('=') for row in old for v in row):
                raise MirrorConflict('MODEL3_EXISTING_FORMULAS_NEED_REVIEW')
            report,planned=plan_ri_indicator_backfill(
                candles=candles,daily=daily,existing=old,timeframe=timeframe,
                from_date=from_date,till_date=till_date,max_rows=max_rows,
                calculated_at=self.now().isoformat())
            if include_minute_proxies and planned:
                from birzha.application.model3_google_backfill import minute_features_from_existing_drive
                proxy, minute_evidence=minute_features_from_existing_drive(
                    transport=self.transport,candles=candles,timeframe=timeframe,
                    source_keys=set(report['source_keys']),till_date=till_date)
                report,planned=plan_ri_indicator_backfill(
                    candles=candles,daily=daily,existing=old,timeframe=timeframe,
                    from_date=from_date,till_date=till_date,max_rows=max_rows,
                    calculated_at=report['calculated_at'],minute_features=proxy)
                report['minute_evidence']=minute_evidence
            if dry_run or not planned:
                return report
            if report['coverage']['orphan_indicator_records']:
                raise MirrorConflict('MODEL3_ORPHANS_REQUIRE_REVIEW')
            expected_source=digest(candles)
            expected_daily=digest(daily)
            expected_old=digest(old)
            # One authorized writer is required; this is an extra optimistic
            # check but Google has no CAS across read/insert.
            if digest(self._schemas('RI',timeframe))!=expected_source:
                raise MirrorConflict('MODEL3_SOURCE_CHANGED')
            if timeframe!='D1' and digest(self._schemas('RI','D1'))!=expected_daily:
                raise MirrorConflict('MODEL3_DAILY_SOURCE_CHANGED')
            if digest(self.transport.read_rows('RI',sheet))!=expected_old:
                raise MirrorConflict('MODEL3_INDICATORS_CHANGED')
            pos=report['insert_row_index']
            try:
                self.transport.model3_insert_rows('RI',sheet,pos,planned)
            except Exception:
                # Never automatically repeat an insert with unknown outcome.
                raise MirrorConflict('MODEL3_INSERT_UNKNOWN_RECONCILE_KEYS') from None
            after=self.transport.read_rows('RI',sheet)
            expected=old[:pos]+planned+old[pos:]
            width=len(old[0])
            normalized=lambda table:[list(row)+['']*(width-len(row)) for row in table]
            if normalized(after)!=normalized(expected):
                raise MirrorConflict('MODEL3_INSERT_READBACK_CONFLICT')
            # The batch is already committed; a status-write failure does NOT
            # invalidate or trigger re-submission of these source keys.
            status='READBACK_PASS_RESEARCH_ONLY'
            try:
                self.transport.update_status('RI',{
                    'model3_google_backfill_status':status,
                    'model3_google_backfill_sha':self.source_sha,
                    'model3_google_backfill_timeframe':timeframe,
                    'model3_google_backfill_last_date':till_date,
                    'model3_google_backfill_m1_evidence':packed(report.get('minute_evidence',[])),
                })
            except Exception:
                status='READBACK_PASS_STATUS_UNCONFIRMED'
            return {**report,'status':status,'written':True,'confirmed_rows':len(planned),
                    'forecast_admission':'NOT_GRANTED'}
        finally:
            self.lock.release()

    def coverage(self,*,symbol,timeframes=('D1','H1','M15')):
        symbol=GoogleSheetsBridge.target_for_symbol(symbol)[0]
        state=self._status(symbol)
        result={}
        for tf in timeframes:
            if tf not in ('D1','H1','M15'):raise MirrorConflict('TIMEFRAME_UNSUPPORTED')
            table=self._schemas(symbol,tf);h=table[0]
            dates=[stamp(r[h.index('bar_start_time')]).date().isoformat() for r in table[1:] if stamp(r[h.index('bar_start_time')]).date()<self.now().astimezone(MSK).date()]
            contracts=sorted({r[h.index('contract_code')] for r in table[1:]})
            result[tf]={'rows':len(dates),'first_date':min(dates) if dates else None,
                'last_date':max(dates) if dates else None,'contracts':contracts,
                'archive_start':ARCHIVE_START_DATE.isoformat(),
                'scan_cursor':self._scan_cursor(symbol,tf,state).isoformat(),
                'full_session_coverage':'UNVERIFIED'}
        return {'symbol':symbol,'timeframes':result,'canonical_storage':'GOOGLE_SHEETS','written':False}
