"""Bounded SBProX blank-cell application through the existing native writer.

Preview by default. No schema creation, source mutation, automatic retry, or
claim of distributed exclusion: the owner must exclude external editors.
"""
from copy import deepcopy
import uuid

from birzha.application.canonical_market_mirror import MirrorConflict, digest
from birzha.application.model3_write_audit import normalized, record, recover
from birzha.application.model3_indicator_coverage import RI_SBPROX_INDICATOR_COLUMNS
from birzha.application.sbprox_integration import aggregate, sheet_plan

SCHEMA_SHA256 = digest([list(RI_SBPROX_INDICATOR_COLUMNS)])
OPERATION = 'SBPROX_BLANK_CELL_REPAIR'


def prepare(service, *, exact_secid, timeframe, start, end, max_cells=12):
    if timeframe not in ('D1', 'H1', 'M15'):
        raise MirrorConflict('SBPROX_TIMEFRAME')
    if type(max_cells) is not int or not 1 <= max_cells <= 200:
        raise MirrorConflict('SBPROX_MAX_CELLS_1_200')
    sheet = timeframe + '_INDICATORS'
    candles = service._schemas('RI', timeframe)
    service.transport.validate_model3_target('RI', sheet)
    old = normalized(service.transport.read_rows('RI', sheet))
    source = service.transport.read_sbprox_master()
    result = aggregate(source, exact_secid, timeframe, start, end, service.now())
    plan = sheet_plan(result, candles, old)
    candidates = [c for r in plan['rows'] for c in r['cells']]
    selected = candidates[:max_cells]
    edits = [(c['row_index'], c['column_index'], c['value']) for c in selected]
    plan.update(planned_cells=len(edits), remaining_cells=len(candidates)-len(edits),
                selected_cells=selected, timeframe=timeframe,
                required_schema_sha256=SCHEMA_SHA256,
                forecast_admission='NOT_GRANTED', mode='DRY_RUN')
    plan['plan_sha256'] = digest([[plan['plan_sha256'], digest(candles), digest(old),
        exact_secid, timeframe, start, end, max_cells, SCHEMA_SHA256]])
    return plan, edits, candles, old


def repair(service, *, exact_secid, timeframe, start, end, max_cells=12,
           dry_run=True, owner_confirmed=False, external_writer_exclusion_confirmed=False,
           expected_plan_sha256='', approved_schema_sha256=''):
    if any(type(v) is not bool for v in (dry_run, owner_confirmed, external_writer_exclusion_confirmed)):
        raise MirrorConflict('SBPROX_EXPLICIT_BOOLEANS_REQUIRED')
    args = dict(exact_secid=exact_secid,timeframe=timeframe,start=start,end=end,max_cells=max_cells)
    if dry_run:
        return prepare(service, **args)[0]
    if (not service.sole_writer or not owner_confirmed or not external_writer_exclusion_confirmed
            or not expected_plan_sha256 or approved_schema_sha256 != SCHEMA_SHA256):
        raise MirrorConflict('SBPROX_OWNER_WRITER_EXCLUSION_SCHEMA_PLAN_REQUIRED')
    if timeframe not in ('D1','H1','M15'):
        raise MirrorConflict('SBPROX_TIMEFRAME')
    sheet = timeframe + '_INDICATORS'
    with service.transport.writer_guard('RI'):
        if not service.lock.acquire(blocking=False):
            raise MirrorConflict('ARCHIVE_BUSY')
        run = uuid.uuid4().hex
        try:
            # Reconcile shared pending receipts before calculating or sending another patch.
            record(service, run, sheet, 'STARTED', payload={'operation':OPERATION})
            recovered = recover(service)
            if recovered is not None:
                return recovered
            plan, edits, candles, old = prepare(service, **args)
            if tuple(old[0]) != RI_SBPROX_INDICATOR_COLUMNS:
                raise MirrorConflict('SBPROX_EXISTING_APPROVED_COLUMNS_REQUIRED')
            if plan['plan_sha256'] != expected_plan_sha256:
                raise MirrorConflict('SBPROX_PLAN_CHANGED')
            if not edits:
                record(service, run, sheet, 'NO_MISSING', payload={'operation':OPERATION})
                return plan
            expected = deepcopy(old)
            for r,c,v in edits:
                if expected[r][c] not in ('', None) or c < 95:
                    raise MirrorConflict('SBPROX_BLANK_EXTENSION_ONLY')
                expected[r][c] = v
            before, after = digest(old), digest(expected)
            record(service, run, sheet, 'PREPARED', before=before, after=after, count=len(edits),
                   payload={'operation':OPERATION, 'plan_sha256':expected_plan_sha256,
                            'source_sha256':plan['source_sha256'], 'schema_sha256':SCHEMA_SHA256,
                            'cells':[[r,c] for r,c,v in edits]})
            if (normalized(service.transport.read_rows('RI',sheet)) != old
                    or digest(service._schemas('RI',timeframe)) != digest(candles)
                    or service.transport.read_sbprox_master()['sha256'] != plan['source_sha256']):
                raise MirrorConflict('SBPROX_CONCURRENT_CHANGE_NO_WRITE')
            try:
                service.transport.model3_write_cells('RI',sheet,edits,existing_only=True,sbprox_only=True)
            except Exception:
                raise MirrorConflict('SBPROX_UNKNOWN_OUTCOME_NO_RETRY') from None
            if normalized(service.transport.read_rows('RI',sheet)) != expected:
                raise MirrorConflict('SBPROX_READBACK_CONFLICT_NO_RETRY')
            record(service, run, sheet, 'COMMITTED',before=before,after=after,count=len(edits),
                   payload={'operation':OPERATION})
            status = 'READBACK_PASS_RESEARCH_ONLY'
            try:
                service.transport.update_status('RI',{'sbprox_status':status,
                    'sbprox_source_sha256':plan['source_sha256'], 'sbprox_plan_sha256':expected_plan_sha256,
                    'sbprox_runtime_sha':service.source_sha, 'sbprox_timeframe':timeframe})
            except Exception:
                status = 'READBACK_PASS_STATUS_UNCONFIRMED'
                record(service,run,sheet,'STATUS_UNCONFIRMED',payload={'operation':OPERATION})
            return {**plan,'mode':'APPLY','written':True,'confirmed_cells':len(edits),'status':status}
        except Exception:
            record(service,run,sheet,'ERROR',payload={'operation':OPERATION})
            raise
        finally:
            service.lock.release()
