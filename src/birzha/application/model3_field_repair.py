"""Blank-only repair orchestration using the existing workbook guard and receipts."""
import uuid
from copy import deepcopy
from datetime import date
from zoneinfo import ZoneInfo
from birzha.application.canonical_market_mirror import MirrorConflict, digest
from birzha.application.model3_write_audit import normalized, record, recover
from birzha.application.model3_google_backfill import plan_ri_field_repair


def repair_fields(service, *, timeframe, from_date, till_date, max_rows=50,
                  max_cells=50, fields=None, after_key='', dry_run=True,
                  owner_confirmed=False, external_writer_exclusion_confirmed=False,
                  expected_plan_sha256=''):
    if any(type(v) is not bool for v in (dry_run,owner_confirmed,external_writer_exclusion_confirmed)):
        raise MirrorConflict('MODEL3_EXPLICIT_BOOLEANS_REQUIRED')
    if timeframe not in ('D1','H1','M15'):raise MirrorConflict('MODEL3_TIMEFRAME_UNSUPPORTED')
    if date.fromisoformat(till_date)>=service.now().astimezone(ZoneInfo("Europe/Moscow")).date():
        raise MirrorConflict('MODEL3_COMPLETED_DAYS_ONLY')
    if not dry_run and (not service.sole_writer or not owner_confirmed
            or not external_writer_exclusion_confirmed or not expected_plan_sha256):
        raise MirrorConflict('MODEL3_REPAIR_EXPLICIT_OWNERSHIP_PLAN_AND_EXTERNAL_EXCLUSION_REQUIRED')
    if type(max_rows) is not int or not 1 <= max_rows <= 200:
        raise MirrorConflict('MODEL3_REPAIR_MCP_ROWS_BOUND_200')
    args=dict(timeframe=timeframe,from_date=from_date,till_date=till_date,
              max_rows=max_rows,max_cells=max_cells,fields=fields,after_key=after_key)
    if dry_run:return _run(service,args,True)
    # Same per-book OS lease as append/M1/other native mutations. No expiry stealing.
    with service.transport.writer_guard('RI'):
        if not service.lock.acquire(blocking=False):raise MirrorConflict('ARCHIVE_BUSY')
        run=uuid.uuid4().hex;sheet=timeframe+'_INDICATORS'
        try:
            record(service,run,sheet,'STARTED',payload={'operation':'BLANK_CELL_REPAIR'})
            recovered=recover(service)
            if recovered is not None:
                record(service,run,sheet,'RECOVERY_COMPLETE',payload={'operation':'BLANK_CELL_REPAIR'})
                return recovered  # no automatic new write after reconciliation
            return _run(service,args,False,run,expected_plan_sha256)
        except Exception:
            record(service,run,sheet,'ERROR',payload={'operation':'BLANK_CELL_REPAIR'})
            raise
        finally:service.lock.release()


def _run(service,args,dry_run,run=None,expected_plan=''):
    tf=args['timeframe'];sheet=tf+'_INDICATORS'
    candles=service._schemas('RI',tf)
    daily=candles if tf=='D1' else service._schemas('RI','D1')
    service.transport.validate_model3_target('RI',sheet)
    old=normalized(service.transport.read_rows('RI',sheet))
    report,edits=plan_ri_field_repair(candles=candles,daily=daily,existing=old,
                                    calculated_at=service.now().isoformat(),**args)
    if dry_run:return report
    if report['plan_sha256']!=expected_plan:raise MirrorConflict('MODEL3_REPAIR_PLAN_CHANGED')
    if not edits:
        record(service,run,sheet,'NO_MISSING',payload={'operation':'BLANK_CELL_REPAIR'})
        return report
    expected=deepcopy(old)
    for r,c,v in edits:
        if expected[r][c] not in ('',None):raise MirrorConflict('MODEL3_REPAIR_OCCUPIED_CELL')
        expected[r][c]=v
    before=digest(old);after=digest(expected)
    record(service,run,sheet,'PREPARED',before=before,after=after,count=len(edits),
           payload={'operation':'BLANK_CELL_REPAIR','plan_sha256':expected_plan,
                    'cells':[[r,c] for r,c,_ in edits]})
    # Re-read AFTER PREPARED; source and complete old-row snapshot must still match.
    # External Google clients cannot be fenced by this host mutex. This check is
    # optimistic, not a Google CAS; exclusion is a separate owner's prerequisite.
    if (normalized(service.transport.read_rows('RI',sheet))!=old
            or digest(service._schemas('RI',tf))!=digest(candles)
            or (tf!='D1' and digest(service._schemas('RI','D1'))!=digest(daily))):
        raise MirrorConflict('MODEL3_REPAIR_CONCURRENT_CHANGE_NO_WRITE')
    try:
        service.transport.model3_write_cells('RI',sheet,edits,existing_only=True)
    except Exception:
        raise MirrorConflict('MODEL3_REPAIR_UNKNOWN_OUTCOME_NO_RETRY') from None
    observed=normalized(service.transport.read_rows('RI',sheet))
    if observed!=expected:raise MirrorConflict('MODEL3_REPAIR_READBACK_CONFLICT_NO_RETRY')
    record(service,run,sheet,'COMMITTED',before=before,after=after,count=len(edits),
           payload={'operation':'BLANK_CELL_REPAIR'})
    status='READBACK_PASS_RESEARCH_ONLY'
    try:
        service.transport.update_status('RI',{'model3_field_repair_status':status,
            'model3_field_repair_sha':service.source_sha,'model3_field_repair_plan':expected_plan,
            'model3_field_repair_timeframe':tf})
    except Exception:
        status='READBACK_PASS_STATUS_UNCONFIRMED'
        record(service,run,sheet,'STATUS_UNCONFIRMED',count=len(edits))
    return {**report,'status':status,'written':True,'confirmed_cells':len(edits)}
