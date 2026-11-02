"""Receipts in the existing QUALITY_AUDIT; never records exception text/secrets."""
import json
import uuid
from birzha.application.canonical_market_mirror import MirrorConflict, digest

CLOSED = {'COMMITTED', 'RECOVERED_COMMITTED'}

def normalized(rows):
    from birzha.application.model3_indicator_coverage import COMPACT_RI_INDICATOR_COLUMNS
    if not rows or tuple(rows[0]) != COMPACT_RI_INDICATOR_COLUMNS or len(rows)>100000:
        raise MirrorConflict('MODEL3_RECEIPT_HEADER_INVALID')
    if any(len(r)>95 for r in rows):raise MirrorConflict('MODEL3_RECEIPT_ROW_INVALID')
    return [[('' if v is None else v) for v in list(r)+['']*(95-len(r))] for r in rows]

def events(transport):
    transport.validate_service_target('RI', 'QUALITY_AUDIT')
    rows = transport.read_service_rows('RI', 'QUALITY_AUDIT')
    from birzha.storage.google_archive_transport import AUDIT
    if not rows or rows[0] != AUDIT:
        raise MirrorConflict('MODEL3_AUDIT_SCHEMA_REQUIRED')
    if any(len(r) > len(AUDIT) for r in rows[1:]):
        raise MirrorConflict('MODEL3_AUDIT_ROWS_INVALID')
    return [dict(zip(AUDIT, list(r) + [''] * (len(AUDIT) - len(r)))) for r in rows[1:]
            if len(r) > 1 and str(r[1]).startswith('MODEL3|')]

def record(service, run, sheet, status, *, before='', after='', payload=None, count=0):
    service.transport.append_quality('RI', {
        'audit_id': run + ('~' + uuid.uuid4().hex if status.startswith('RECOVERY_') else '') + '|' + status, 'scope': 'MODEL3|' + sheet,
        'status': status, 'description': 'RI research append-only backfill',
        'checked_at': service.now().isoformat(), 'evidence_ref': service.source_sha,
        'affected_rows': count, 'before_sha': before, 'after_sha': after,
        'limitation': 'LOCAL_COOPERATING_WRITERS_ONLY;RESEARCH_ONLY',
        'next_action': 'RECONCILE_BEFORE_RETRY' if status in ('PREPARED', 'ERROR') else '',
        'owner': 'MCP', 'source_sheet': sheet,
        'source_payload_json': json.dumps(payload or {}, sort_keys=True, separators=(',', ':')),
    })

def recover(service):
    records = events(service.transport)
    closed = {r['audit_id'].rsplit('|', 1)[0] for r in records if r['status'] in CLOSED}
    pending = [r for r in records if r['status'] == 'PREPARED' and r['audit_id'].rsplit('|', 1)[0] not in closed]
    if len(pending) > 1:
        raise MirrorConflict('MODEL3_MULTIPLE_PENDING_RECEIPTS')
    if not pending: return None
    r = pending[0]; run = r['audit_id'].rsplit('|', 1)[0]; sheet = r['source_sheet']
    if sheet not in ('D1_INDICATORS', 'H1_INDICATORS', 'M15_INDICATORS'):
        raise MirrorConflict('MODEL3_RECOVERY_TARGET_INVALID')
    service.transport.validate_model3_target('RI', sheet)
    current = digest(normalized(service.transport.read_rows('RI', sheet)))
    if current == r['after_sha']:
        status = 'RECOVERED_COMMITTED'; written = True
    elif current == r['before_sha']:
        # A read showing no new rows does NOT exclude a late Google commit.
        # Keep PREPARED unresolved; never release a fresh append automatically.
        record(service, run, sheet, 'RECOVERY_NOT_OBSERVED', before=current)
        raise MirrorConflict('MODEL3_RECOVERY_SETTLEMENT_REQUIRED_NO_RETRY')
    else:
        record(service, run, sheet, 'RECOVERY_CONFLICT')
        raise MirrorConflict('MODEL3_RECOVERY_CONFLICT_NO_WRITE')
    record(service, run, sheet, status, before=r['before_sha'], after=current, count=r['affected_rows'])
    # Recovery performs NO indicator write; the caller must review before a new batch.
    return {'status': status, 'written': written, 'recovery_only': True,
            'confirmed_rows': r['affected_rows'] if written else 0,
            'forecast_admission': 'NOT_GRANTED'}
