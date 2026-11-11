"""Run one explicit RI SBProX month without a scheduler or local data store."""

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from threading import Lock, Thread
import uuid

from birzha.application.canonical_market_mirror import MirrorConflict, digest
from birzha.application.model3_indicator_coverage import RI_SBPROX_INDICATOR_COLUMNS
from birzha.application.model3_write_audit import normalized, record, recover
from birzha.application.sbprox_field_repair import OPERATION, SCHEMA_SHA256, verify_moex_references
from birzha.application.sbprox_integration import aggregate, indexed_minute_reference, sheet_plan
from birzha.application.sbprox_moex_comparison import moscow
from birzha.providers.sbprox_ri import MOSCOW, SERIES

SHEET = 'H1_INDICATORS'
ROWS_PER_BATCH = 100


def month_window(month, now):
    try:
        start = date.fromisoformat(month + '-01')
    except (TypeError, ValueError):
        raise MirrorConflict('SBPROX_MONTH_FORMAT') from None
    if start.strftime('%Y-%m') != month or start.year != 2026:
        raise MirrorConflict('SBPROX_MONTH_FORMAT')
    stop = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    a = datetime.combine(start, datetime.min.time(), MOSCOW)
    b = datetime.combine(stop, datetime.min.time(), MOSCOW)
    if b > now.astimezone(MOSCOW):
        raise MirrorConflict('SBPROX_COMPLETED_MONTH_REQUIRED')
    return a, b


def read_indicators(transport):
    transport.validate_model3_target('RI', SHEET)
    rows = normalized(transport.read_rows('RI', SHEET))
    if tuple(rows[0]) != RI_SBPROX_INDICATOR_COLUMNS:
        raise MirrorConflict('SBPROX_EXISTING_APPROVED_COLUMNS_REQUIRED')
    return rows


def make_plan(service, month, a, b):
    transport = service.transport
    candles = service._schemas('RI', 'H1')
    old = read_indicators(transport)
    header = {field: i for i, field in enumerate(old[0])}
    targets = {}
    for row_number, row in enumerate(old[1:], 1):
        if not str(row[header['begin']]).startswith(month + '-'):
            continue
        contract = row[header['secid']]
        begin = moscow(row[header['begin']])
        if row[header['symbol']] != 'RI' or row[header['timeframe']] != 'H1' or contract not in SERIES.values():
            raise MirrorConflict('SBPROX_MONTH_TARGET_IDENTITY')
        key = contract, begin
        if key in targets:
            raise MirrorConflict('SBPROX_DUPLICATE_EXACT_CONTRACT_HOUR')
        targets[key] = row_number
    if not targets:
        raise MirrorConflict('SBPROX_MONTH_EXISTING_ROWS_REQUIRED')

    contracts = sorted({key[0] for key in targets})
    source = transport.read_sbprox_master()
    reference = indexed_minute_reference(transport)
    rows = []
    left = a
    while left < b:
        right = min(left + timedelta(days=7), b)
        for secid in contracts:
            result = aggregate(source, secid, 'H1', left.isoformat(), right.isoformat(),
                               service.now(), reference_minutes=reference)
            rows.extend(r for r in result['rows'] if (r['secid'], moscow(r['begin'])) in targets)
        left = right
    base = sheet_plan({'rows': rows, 'source_sha256': source['sha256']}, candles, old)
    by_key = {(r['secid'], moscow(r['begin'])): r for r in base['rows']}
    by_hour = defaultdict(set)
    for secid, begin in targets:
        by_hour[begin].add(secid)
    gaps = []
    for (secid, begin), number in targets.items():
        candidate = by_key.get((secid, begin))
        reasons = candidate['reasons'] if candidate else ['NO_SBPROX_WINDOW']
        if reasons:
            other_observed = sorted(c for c in by_hour[begin]
                                    if c != secid and (c, begin) in by_key)
            gaps.append(dict(sheet_row=number+1, secid=secid, begin=begin.isoformat(),
                             reasons=reasons, other_contracts_observed=other_observed))
    selected = sorted((r for r in base['rows'] if r['cells']),
                      key=lambda r: r['cells'][0]['row_index'])
    report = dict(month=month, sheet=SHEET, contracts=contracts, target_rows=len(targets),
                  distinct_hours=len(by_hour), parallel_contract_hours=sum(len(c)>1 for c in by_hour.values()),
                  planned_rows=len(selected), planned_cells=sum(len(r['cells']) for r in selected),
                  gap_rows=len(gaps), gap_counts=dict(Counter(x for g in gaps for x in g['reasons'])),
                  other_contract_observed_gap_rows=sum(bool(g['other_contracts_observed']) for g in gaps),
                  gaps=gaps, source_sha256=source['sha256'], forecast_admission='NOT_GRANTED')
    report['plan_sha256'] = digest([[month, SCHEMA_SHA256, base['plan_sha256'], digest(candles), digest(old)]])
    return report, selected, old, digest(candles)


def apply_batches(service, report, selected, old, candle_sha, progress):
    transport = service.transport
    total = 0
    for batch_n, start in enumerate(range(0, len(selected), ROWS_PER_BATCH), 1):
        expected = deepcopy(old)
        edits = []
        references = {}
        for row in selected[start:start+ROWS_PER_BATCH]:
            ref = row['moex_m1_reference']
            if ref:
                references[(ref['secid'], ref['month'])] = ref
            for cell in row['cells']:
                r, c, v = cell['row_index'], cell['column_index'], cell['value']
                if c < 95 or expected[r][c] != '':
                    raise MirrorConflict('SBPROX_BLANK_EXTENSION_ONLY')
                expected[r][c] = v
                edits.append((r,c,v))
        run = uuid.uuid4().hex
        before, after = digest(old), digest(expected)
        payload = dict(operation=OPERATION, period=report['month'], batch=batch_n,
                       plan_sha256=report['plan_sha256'], source_sha256=report['source_sha256'],
                       moex_m1_references=list(references.values()),
                       rows=sorted({r+1 for r,c,v in edits}))
        record(service, run, SHEET, 'STARTED', payload=payload)
        record(service, run, SHEET, 'PREPARED', before=before, after=after, count=len(edits), payload=payload)
        try:
            if (read_indicators(transport) != old
                    or digest(service._schemas('RI', 'H1')) != candle_sha
                    or transport.read_sbprox_master()['sha256'] != report['source_sha256']):
                raise MirrorConflict('SBPROX_CONCURRENT_CHANGE_NO_WRITE')
            verify_moex_references(transport, list(references.values()))
        except Exception:
            record(service, run, SHEET, 'ABORTED_NO_WRITE', before=before, after=after, payload=payload)
            raise
        try:
            transport.model3_write_cells('RI', SHEET, edits, existing_only=True, sbprox_only=True)
        except Exception:
            raise MirrorConflict('SBPROX_UNKNOWN_OUTCOME_NO_RETRY') from None
        observed = read_indicators(transport)
        if observed != expected:
            raise MirrorConflict('SBPROX_READBACK_CONFLICT_NO_RETRY')
        record(service, run, SHEET, 'COMMITTED', before=before, after=after, count=len(edits), payload=payload)
        total += len(edits)
        progress(confirmed_cells=total, confirmed_batches=batch_n)
        old = observed
    status = 'READBACK_PASS_RESEARCH_ONLY' if total else 'NO_MISSING'
    if total:
        try:
            transport.update_status('RI', {'sbprox_status':status, 'sbprox_last_period':report['month'],
                'sbprox_plan_sha256':report['plan_sha256'], 'sbprox_source_sha256':report['source_sha256'],
                'sbprox_runtime_sha':service.source_sha, 'sbprox_timeframe':'H1'})
        except Exception:
            status = 'READBACK_PASS_STATUS_UNCONFIRMED'
    return dict(report, status=status, written=bool(total), confirmed_cells=total)


def hourly_series(service, month):
    """Read one research row per hour using the historical MOEX resolver."""
    resolver = getattr(service.market, 'historical_future_resolver', None)
    if resolver is None:
        raise MirrorConflict('RI_HISTORICAL_RESOLVER_REQUIRED')
    candles = service._schemas('RI', 'H1')
    fields = candles[0]
    source = {r[fields.index('record_key')]: dict(zip(fields,r)) for r in candles[1:]}
    rows = read_indicators(service.transport)
    groups = defaultdict(dict)
    for number, values in enumerate(rows[1:], 2):
        row = dict(zip(rows[0],values))
        if not str(row['begin']).startswith(month+'-'):
            continue
        begin = moscow(row['begin'])
        if row['symbol'] != 'RI' or row['timeframe'] != 'H1' or row['secid'] in groups[begin]:
            raise MirrorConflict('SBPROX_MONTH_TARGET_IDENTITY')
        candle = source.get(row['source_record_key'])
        if (not candle or candle['contract_code'] != row['secid']
                or moscow(candle['bar_start_time']) != begin):
            raise MirrorConflict('RI_HOURLY_SOURCE_LINK_CONFLICT')
        groups[begin][row['secid']] = (number, row, candle)

    decisions = {}
    result = []
    for begin, candidates in sorted(groups.items()):
        dates = {c.get('trade_session_date') for _,_,c in candidates.values()}
        output = dict(begin=begin.isoformat(), available_contracts=sorted(candidates))
        if len(dates) != 1 or not next(iter(dates)):
            result.append(dict(output,status='TRADE_SESSION_UNVERIFIED'))
            continue
        session = next(iter(dates))
        if session not in decisions:
            try:
                instrument = resolver.resolve('RI',date.fromisoformat(str(session)))
                decisions[session] = (instrument.secid
                    if instrument.source == 'MOEX_ISS_HISTORY_T_MINUS_1_RECONSTRUCTED' else None)
            except (ValueError, RuntimeError):
                decisions[session] = None
        secid = decisions[session]
        output.update(trade_session_date=session, selected_contract=secid)
        if secid is None or secid not in candidates:
            result.append(dict(output,status='T_MINUS_1_UNVERIFIED' if secid is None else 'SELECTED_CONTRACT_MISSING'))
            continue
        number,row,candle = candidates[secid]
        values = {f:row[f] for f in RI_SBPROX_INDICATOR_COLUMNS[95:] if row[f] not in ('',None)}
        result.append(dict(output, sheet_row=number, end=row['end'],
                           status='RESEARCH_ONLY' if values else 'SBPROX_MISSING', values=values))
    return dict(month=month, status='RESEARCH_SERIES', written=False, rows=result,
                distinct_hours=len(groups), counts=dict(Counter(r['status'] for r in result)),
                forecast_admission='NOT_GRANTED', historical_session_attestation='UNVERIFIED')


class MonthlyJobs:
    """Bounded background invocation. Durable write receipts remain in Google."""

    def __init__(self, service, *, allow_write=False):
        self.service = service
        self.allow_write = allow_write or getattr(service, "sole_writer", False)
        self.lock = Lock()
        self.jobs = {}

    def start(self, month, dry_run, owner_confirmed, external_writer_exclusion_confirmed,
              expected_plan_sha256):
        a,b = month_window(month,self.service.now())
        if any(type(v) is not bool for v in (dry_run, owner_confirmed, external_writer_exclusion_confirmed)):
            raise MirrorConflict('SBPROX_EXPLICIT_BOOLEANS_REQUIRED')
        if not dry_run and (not self.allow_write or not owner_confirmed
                            or not external_writer_exclusion_confirmed or not expected_plan_sha256):
            raise MirrorConflict('SBPROX_MONTH_OWNER_WRITER_EXCLUSION_PLAN_REQUIRED')
        return self._launch(month, dry_run, self._run, (month,a,b,dry_run,expected_plan_sha256))

    def start_series(self, month):
        month_window(month, self.service.now())
        return self._launch(month, True, self._run_series, (month,))

    def _launch(self, month, dry_run, worker, args):
        with self.lock:
            if any(j['job_state'] == 'RUNNING' for j in self.jobs.values()):
                raise MirrorConflict('SBPROX_MONTH_JOB_BUSY')
            if len(self.jobs) >= 8:
                del self.jobs[next(iter(self.jobs))]
            job = uuid.uuid4().hex
            self.jobs[job] = dict(job_id=job, month=month, dry_run=dry_run, job_state='RUNNING',
                                  confirmed_cells=0, confirmed_batches=0)
        Thread(target=worker, args=(job,*args), daemon=True).start()
        return self.status(job)

    def status(self, job_id, offset=0, limit=50):
        if type(offset) is not int or type(limit) is not int or offset<0 or not 1<=limit<=100:
            return {'job_state':'BLOCKED','code':'SBPROX_MONTH_PAGE_INVALID'}
        with self.lock:
            job = deepcopy(self.jobs.get(job_id, {'job_state':'UNKNOWN_JOB',
                'next_action':'CHECK_QUALITY_AUDIT_AND_PREVIEW_BEFORE_RETRY'}))
        result = job.get('result',{})
        for field in ('gaps','rows'):
            if field in result:
                records = result[field]
                result[field] = records[offset:offset+limit]
                result['next_offset'] = offset+limit if offset+limit<len(records) else None
        return job

    def _run_series(self, job, month):
        try:
            result = hourly_series(self.service, month)
            fields = dict(job_state='DONE',result=result)
        except Exception:
            fields = dict(job_state='BLOCKED',code='RI_HOURLY_SERIES_FAILED',written=False)
        with self.lock:
            self.jobs[job].update(fields)

    def _run(self, job, month, a, b, dry_run, expected):
        def update(**fields):
            with self.lock:
                self.jobs[job].update(fields)
        s = self.service
        try:
            if dry_run:
                report = make_plan(s, month, a, b)[0]
                result = dict(report, status='DRY_RUN', written=False)
            else:
                with s.transport.writer_guard('RI'):
                    if not s.lock.acquire(blocking=False):
                        raise MirrorConflict('ARCHIVE_BUSY')
                    try:
                        recovered = recover(s)
                        if recovered is not None:
                            result = recovered
                        else:
                            report, selected, old, candle_sha = make_plan(s, month, a, b)
                            if report['plan_sha256'] != expected:
                                raise MirrorConflict('SBPROX_MONTH_PLAN_CHANGED')
                            result = apply_batches(s, report, selected, old, candle_sha, update)
                    finally:
                        s.lock.release()
            update(job_state='DONE', result=result)
        except Exception as exc:
            code = str(exc) if isinstance(exc, MirrorConflict) and str(exc).isupper() and str(exc).replace('_','').isalnum() else 'SBPROX_MONTH_FAILED'
            update(job_state='BLOCKED', code=code, written=False if dry_run else 'CHECK_QUALITY_AUDIT',
                   next_action='RECONCILE_BEFORE_RETRY' if not dry_run else 'CHECK_INPUTS')
