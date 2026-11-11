"""Run RI SBProX backfill inside the archive MCP."""

from collections import Counter, defaultdict
from functools import lru_cache
from copy import deepcopy
from datetime import date, datetime, timedelta
from threading import Lock, Thread
import uuid

from birzha.application.canonical_market_mirror import MirrorConflict, digest
from birzha.application.model3_indicator_coverage import RI_SBPROX_INDICATOR_COLUMNS
from birzha.application.model3_write_audit import normalized, record, recover
from birzha.application.sbprox_field_repair import OPERATION, SCHEMA_SHA256, drive_archive_matches, verify_moex_references
from birzha.application.sbprox_integration import (
    OUTPUTS, aggregate_open_master, indexed_minute_reference, open_master, sheet_plan,
)
from birzha.application.sbprox_moex_comparison import moscow
from birzha.providers.sbprox_ri import MOSCOW, SERIES

SHEET = 'H1_INDICATORS'
ROWS_PER_BATCH = 100


def month_window(month, now, *, include_open_month=False):
    try:
        start = date.fromisoformat(month + '-01')
    except (TypeError, ValueError):
        raise MirrorConflict('SBPROX_MONTH_FORMAT') from None
    if start.strftime('%Y-%m') != month or start.year != 2026:
        raise MirrorConflict('SBPROX_MONTH_FORMAT')
    stop = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    a = datetime.combine(start, datetime.min.time(), MOSCOW)
    b = datetime.combine(stop, datetime.min.time(), MOSCOW)
    closed = now.astimezone(MOSCOW).replace(minute=0, second=0, microsecond=0)
    if include_open_month:
        b = min(b, closed)
        if a >= b:
            raise MirrorConflict('SBPROX_NO_COMPLETED_HOUR_IN_RANGE')
    elif b > closed:
        raise MirrorConflict('SBPROX_COMPLETED_MONTH_REQUIRED')
    return a, b


def read_indicators(transport):
    transport.validate_model3_target('RI', SHEET)
    rows = normalized(transport.read_rows('RI', SHEET))
    if tuple(rows[0]) != RI_SBPROX_INDICATOR_COLUMNS:
        raise MirrorConflict('SBPROX_EXISTING_APPROVED_COLUMNS_REQUIRED')
    return rows


def sync_window(now, month, year, from_date, till_date):
    """Choose explicit inclusive dates or scan the source year for holes."""
    first, limit = date(2026, 1, 1), date(2027, 1, 1)
    closed = now.astimezone(MOSCOW).replace(minute=0, second=0, microsecond=0)
    midnight = lambda day: datetime.combine(day, datetime.min.time(), MOSCOW)
    if month:
        if year is not None or from_date or till_date:
            raise MirrorConflict('SBPROX_PERIOD_ARGUMENTS_CONFLICT')
        a, b = month_window(month, now, include_open_month=True)
        return month, a, b, False
    if year is not None:
        if from_date or till_date:
            raise MirrorConflict('SBPROX_PERIOD_ARGUMENTS_CONFLICT')
        if year != 2026:
            raise MirrorConflict('SBPROX_SOURCE_YEAR_REQUIRED')
        return str(year), midnight(first), min(midnight(limit), closed), False
    if not from_date and not till_date:
        return 'update', midnight(first), min(midnight(limit), closed), True
    try:
        start, finish = date.fromisoformat(from_date), date.fromisoformat(till_date)
        if from_date != start.isoformat() or till_date != finish.isoformat():
            raise ValueError
    except (TypeError, ValueError):
        raise MirrorConflict('SBPROX_EXPLICIT_DATE_RANGE_REQUIRED') from None
    if not first <= start <= finish < limit:
        raise MirrorConflict('SBPROX_DATE_RANGE_OUTSIDE_SOURCE_YEAR')
    a, b = midnight(start), min(midnight(finish + timedelta(days=1)), closed)
    if a >= b:
        raise MirrorConflict('SBPROX_NO_COMPLETED_HOUR_IN_RANGE')
    return f'{start}/{finish}', a, b, False


def observed_hours(service, source, windows, reference):
    """Attach observations only to exact intervals already stored in the RI book."""
    hours = []
    if not windows:
        return hours
    by_contract = defaultdict(dict)
    for (secid, start), finish in windows.items():
        by_contract[secid][start] = finish
    now = service.now()
    with open_master(source) as master:
        for secid, items in sorted(by_contract.items()):
            starts = sorted(items)
            i = 0
            while i < len(starts):
                left = starts[i].replace(second=0, microsecond=0)
                batch = {}
                while i < len(starts) and items[starts[i]] <= left + timedelta(days=7):
                    batch[starts[i]] = items[starts[i]]
                    i += 1
                finish = max(batch.values())
                right = finish.replace(second=0, microsecond=0)
                if right < finish:
                    right += timedelta(minutes=1)
                result = aggregate_open_master(source, master, secid, 'H1', left.isoformat(),
                                               right.isoformat(), now, reference_minutes=reference,
                                               canonical_windows=batch)
                hours.extend(result['rows'])
    return hours


def make_plan(service, period, a, b, *, automatic=False):
    transport = service.transport
    candles = service._schemas('RI', 'H1')
    old = read_indicators(transport)
    header = {field: i for i, field in enumerate(old[0])}
    candle_fields = {field: i for i, field in enumerate(candles[0])}
    windows = {}
    ambiguous = set()
    for row in candles[1:]:
        start = moscow(row[candle_fields['bar_start_time']])
        if not a <= start < b:
            continue
        finish = moscow(row[candle_fields['bar_end_time']])
        if not start < finish <= min(b, start + timedelta(hours=1)):
            continue
        key = row[candle_fields['contract_code']], start
        if key in windows:
            ambiguous.add(key)
        windows[key] = finish

    previous = {}
    for (secid, start), finish in sorted(windows.items()):
        prior = previous.get(secid)
        if prior and start < prior[1]:
            ambiguous.update(((secid, prior[0]), (secid, start)))
        if not prior or finish > prior[1]:
            previous[secid] = (start, finish)

    targets = {}
    complete = set()
    latest_filled = None
    for row_number, row in enumerate(old[1:], 1):
        value = str(row[header['begin']])
        if not value.startswith('2026-'):
            continue
        begin = moscow(value)
        if not a <= begin < b:
            continue
        filled = all(row[header[field]] != '' for field in OUTPUTS)
        if filled and (latest_filled is None or begin > latest_filled):
            latest_filled = begin
        contract = row[header['secid']]
        if row[header['symbol']] != 'RI' or row[header['timeframe']] != 'H1' or contract not in SERIES.values():
            raise MirrorConflict('SBPROX_MONTH_TARGET_IDENTITY')
        key = contract, begin
        if key in targets:
            raise MirrorConflict('SBPROX_DUPLICATE_EXACT_CONTRACT_HOUR')
        targets[key] = row_number
        if filled:
            complete.add(key)

    contracts = sorted({key[0] for key in targets})
    usable = {key: finish for key, finish in windows.items()
              if key in targets and key not in ambiguous
              and moscow(old[targets[key]][header['end']]) == finish}
    source = transport.read_sbprox_master() if usable else {'sha256': None}
    reference = indexed_minute_reference(transport)
    observations = observed_hours(service, source, usable, reference)
    source_first = min((moscow(row['begin']) for row in observations), default=None)
    source_last = max((moscow(row['begin']) for row in observations), default=None)
    observed = {(row['secid'], moscow(row['begin'])): row for row in observations}
    rows = [row for key, row in observed.items() if key not in complete]
    @lru_cache(maxsize=None)
    def archive_matches(file_id, checksum):
        try:
            return drive_archive_matches(transport, file_id, checksum)
        except MirrorConflict as exc:
            if str(exc) in ('Google request refused (HTTP 403); no fallback',
                            'Google request refused (HTTP 404); no fallback'):
                return False
            raise
    base = sheet_plan({'rows': rows, 'source_sha256': source['sha256']}, candles, old,
                      archive_matches=archive_matches)
    by_key = {(r['secid'], moscow(r['begin'])): r for r in base['rows']}
    by_hour = defaultdict(set)
    for secid, begin in targets:
        by_hour[begin].add(secid)
    gaps = []
    for (secid, begin), number in targets.items():
        if (secid, begin) in complete:
            continue
        candidate = by_key.get((secid, begin))
        if (secid, begin) not in usable:
            reasons = ['CANONICAL_WINDOW_UNVERIFIED']
        else:
            reasons = candidate['reasons'] if candidate else ['NO_SBPROX_WINDOW']
        if reasons:
            other_observed = sorted(c for c in by_hour[begin]
                                    if c != secid and (c, begin) in observed)
            gaps.append(dict(sheet_row=number+1, secid=secid, begin=begin.isoformat(),
                             reasons=reasons, other_contracts_observed=other_observed))
    selected = sorted((r for r in base['rows'] if r['cells']),
                      key=lambda r: r['cells'][0]['row_index'])
    write_rows = [r['cells'][0]['row_index'] + 1 for r in selected]
    first_pending = min((moscow(r['begin']) for r in selected), default=None)
    canonical_end = max(usable.values(), default=None)
    report = dict(month=period, sheet=SHEET, contracts=contracts, target_rows=len(targets),
                  from_time=a.isoformat(), before_time=b.isoformat(), automatic=automatic,
                  source_first_hour=source_first.isoformat() if source_first else None,
                  source_last_hour=source_last.isoformat() if source_last else None,
                  last_filled_hour=latest_filled.isoformat() if latest_filled else None,
                  first_pending_hour=first_pending.isoformat() if first_pending else None,
                  already_filled_rows=len(complete), source_without_target_rows=0,
                  canonical_last_end=canonical_end.isoformat() if canonical_end else None,
                  distinct_hours=len(by_hour), parallel_contract_hours=sum(len(c)>1 for c in by_hour.values()),
                  planned_rows=len(selected), planned_cells=sum(len(r['cells']) for r in selected),
                  first_write_row=write_rows[0] if write_rows else None,
                  last_write_row=write_rows[-1] if write_rows else None,
                  gap_rows=len(gaps), gap_counts=dict(Counter(x for g in gaps for x in g['reasons'])),
                  other_contract_observed_gap_rows=sum(bool(g.get('other_contracts_observed')) for g in gaps),
                  gaps=gaps, source_sha256=source['sha256'], forecast_admission='NOT_GRANTED')
    candle_sha = digest(candles)
    target_keys = [[secid, begin.isoformat()] for secid, begin in targets]
    report['plan_sha256'] = digest([[period, a.isoformat(), SCHEMA_SHA256, source['sha256'],
                                     base['plan_sha256'], target_keys, gaps, candle_sha, digest(old)]])
    return report, selected, old, candle_sha


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
                references[(ref['secid'], ref['month'], ref.get('original_file_id'))] = ref
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
        a, b = month_window(month, self.service.now())
        return self._start(month, a, b, False, dry_run, owner_confirmed,
                           external_writer_exclusion_confirmed, expected_plan_sha256)

    def start_sync(self, month, year, from_date, till_date, dry_run, owner_confirmed,
                   external_writer_exclusion_confirmed, expected_plan_sha256):
        period, a, b, automatic = sync_window(self.service.now(), month, year, from_date, till_date)
        return self._start(period, a, b, automatic, dry_run, owner_confirmed,
                           external_writer_exclusion_confirmed, expected_plan_sha256)

    def _start(self, period, a, b, automatic, dry_run, owner_confirmed,
               external_writer_exclusion_confirmed, expected_plan_sha256):
        if any(type(v) is not bool for v in (dry_run, owner_confirmed, external_writer_exclusion_confirmed)):
            raise MirrorConflict('SBPROX_EXPLICIT_BOOLEANS_REQUIRED')
        if not dry_run and (not self.allow_write or not owner_confirmed
                            or not external_writer_exclusion_confirmed or not expected_plan_sha256):
            raise MirrorConflict('SBPROX_MONTH_OWNER_WRITER_EXCLUSION_PLAN_REQUIRED')
        return self._launch(period, dry_run, self._run, (period, a, b, automatic, dry_run, expected_plan_sha256))

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

    def _run(self, job, period, a, b, automatic, dry_run, expected):
        def update(**fields):
            with self.lock:
                self.jobs[job].update(fields)
        s = self.service
        try:
            if dry_run:
                report = make_plan(s, period, a, b, automatic=automatic)[0]
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
                            report, selected, old, candle_sha = make_plan(s, period, a, b, automatic=automatic)
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
