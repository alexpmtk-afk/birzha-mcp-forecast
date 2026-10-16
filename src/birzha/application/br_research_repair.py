"""Bounded BR reconciliation. Research quarantine is invisible to Forecast reads.

No provider/network, no changes to accepted candles, no key reservation in their
table. This administrative repair does not replace the separate archive collector.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from birzha.storage.historical_store import DuckDBHistoricalCandleStore

VERSION = 'BR_RESEARCH_QUARANTINE_V1'
MAX_ROWS = 5000


class RepairError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def instant(value):
    if isinstance(value, bool):
        raise RepairError('INVALID_TIME')
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise RepairError('INVALID_TIME')
        parsed = datetime(1899, 12, 30) + timedelta(seconds=round(value * 86400))
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise RepairError('INVALID_TIME')
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone(timedelta(hours=3))).replace(tzinfo=None)
    return parsed.isoformat(timespec='seconds')


def table_exists(con):
    return bool(con.execute("SELECT 1 FROM information_schema.tables WHERE table_name='br_research_quarantine'").fetchone())


def fingerprint(con):
    # Logical before/after preservation, in the same transaction/snapshot.
    sha = hashlib.sha256()
    cursor = con.execute('SELECT * FROM historical_candles ORDER BY secid,timeframe,begin')
    while rows := cursor.fetchmany(1000):
        for row in rows:
            sha.update(canonical(row).encode('utf-8'))
            sha.update(b'\n')
    return sha.hexdigest()


def validate(payload, expected_sha256):
    if (not isinstance(payload, dict) or len(canonical(payload).encode('utf-8')) > 5000000
            or digest(payload) != expected_sha256):
        raise RepairError('DATASET_SHA_MISMATCH')
    records = payload.get('records')
    if (payload.get('status') != 'PROPOSAL_ONLY_NOT_APPROVED_FOR_IMPORT'
            or payload.get('source_book') != '1y8nMiqmMKv1af_lRQjGPCYDKpz3P1nfojkPjqqa4JjQ'
            or not isinstance(records, list) or not 1 <= len(records) <= MAX_ROWS
            or payload.get('count') != len(records)):
        raise RepairError('INVALID_BR_DATASET')
    output = []
    seen = set()
    for item in records:
        r = item.get('sheet_row', {})
        secid, tf = r.get('contract_code'), item.get('timeframe')
        if (not isinstance(secid, str) or not re.fullmatch(r'BR[FGHJKMNQUVXZ][0-9]', secid)
                or tf not in {'H1', 'M15'} or item.get('forecast_admission') != 'NOT_GRANTED'
                or item.get('record_quality') != 'RESEARCH_NOT_ADMITTED'):
            raise RepairError('INVALID_RESEARCH_IDENTITY_OR_ADMISSION')
        begin, end = instant(r.get('bar_start_time')), instant(r.get('bar_end_time'))
        if (not '2025-01-01' <= begin[:10] <= min('2026-12-31', date.today().isoformat())
                or end < begin or (datetime.fromisoformat(end)-datetime.fromisoformat(begin)).total_seconds() > {'H1': 3599, 'M15': 899}[tf]):
            raise RepairError('INVALID_RESEARCH_INTERVAL')
        key = (secid, tf, begin)
        if key in seen:
            raise RepairError('DUPLICATE_RESEARCH_KEY')
        seen.add(key)
        nums = []
        for field in ('open', 'high', 'low', 'close', 'volume'):
            v = r.get(field)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise RepairError('INVALID_OHLCV')
            nums.append(v)
        o, h, low, close, volume = nums
        if not low <= min(o, close) <= max(o, close) <= h or volume < 0:
            raise RepairError('INVALID_OHLCV')
        # Original source fields are retained verbatim; never promoted to proof.
        output.append((key, end, item, nums))
    return output


def reconcile(store, payload, expected_sha256, *, apply=False):
    if not isinstance(apply, bool):
        raise RepairError('INVALID_APPLY_FLAG')
    if not isinstance(store, DuckDBHistoricalCandleStore):
        raise RepairError('LOCAL_DUCKDB_REQUIRED')
    rows = validate(payload, expected_sha256)
    con = store._connection
    with store._lock:
        con.execute('BEGIN TRANSACTION')
        try:
            before = fingerprint(con)
            accepted = {}
            for s, tf, b, e, o, h, low, c, v in con.execute(
                "SELECT secid,timeframe,begin,end_time,open,high,low,close,volume FROM historical_candles WHERE secid LIKE 'BR%'"
            ).fetchall():
                accepted[(s, tf, instant(b))] = (instant(e), [o, h, low, c, v])
            existing = {}
            if table_exists(con):
                existing = {(s, tf, b): sha for s, tf, b, sha in con.execute(
                    'SELECT secid,timeframe,begin,payload_sha256 FROM br_research_quarantine'
                ).fetchall()}
            pending = []
            skipped_accepted = skipped_research = 0
            for key, end, item, nums in rows:
                if key in accepted:
                    ae, an = accepted[key]
                    if ae != end or any(a is None or not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-8) for a, b in zip(an, nums)):
                        raise RepairError('ACCEPTED_CANDLE_CONFLICT')
                    skipped_accepted += 1
                    continue
                sha = digest(item)
                if key in existing:
                    if existing[key] != sha:
                        raise RepairError('RESEARCH_CANDLE_CONFLICT')
                    skipped_research += 1
                    continue
                pending.append((*key, end, canonical(item), sha, expected_sha256,
                                datetime.now(timezone.utc).isoformat()))
            backup = None
            if apply and pending:
                if store.path != ':memory:':
                    path = Path(store.path).resolve()
                    backup = path.parent / 'repair_backups' / ('br-' + uuid4().hex)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    con.execute("EXPORT DATABASE '" + str(backup).replace("'", "''") + "' (FORMAT PARQUET)")
                con.execute('''CREATE TABLE IF NOT EXISTS br_research_quarantine (
                    secid VARCHAR NOT NULL, timeframe VARCHAR NOT NULL,
                    begin VARCHAR NOT NULL, end_time VARCHAR NOT NULL,
                    payload_json VARCHAR NOT NULL, payload_sha256 VARCHAR NOT NULL,
                    dataset_sha256 VARCHAR NOT NULL, imported_at VARCHAR NOT NULL,
                    PRIMARY KEY(secid,timeframe,begin))''')
                con.executemany('INSERT INTO br_research_quarantine VALUES (?,?,?,?,?,?,?,?)', pending)
                for s, tf, b, e, data, sha, ds, observed in pending:
                    got = con.execute('SELECT payload_json,payload_sha256 FROM br_research_quarantine WHERE secid=? AND timeframe=? AND begin=?', [s, tf, b]).fetchone()
                    if got != (data, sha):
                        raise RepairError('WRITE_READBACK_MISMATCH')
            after = fingerprint(con)
            if before != after:
                raise RepairError('ACCEPTED_HISTORY_CHANGED')
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
    return {'status': 'RESEARCH_APPLIED' if apply else 'DRY_RUN', 'version': VERSION,
            'dataset_sha256': expected_sha256, 'candidate_count': len(rows),
            'inserted': len(pending) if apply else 0, 'would_insert': len(pending),
            'already_accepted': skipped_accepted, 'already_research': skipped_research,
            'accepted_history_unchanged': before == after, 'accepted_history_sha256': after,
            'backup_directory': str(backup) if backup else None,
            'forecast_admission': 'NOT_GRANTED', 'calendar_status': 'UNVERIFIED',
            'historical_availability_status': 'UNVERIFIED', 'price_adjustment': 'NONE'}


def research_series(store, secid, timeframe, from_date, till_date):
    if not isinstance(store, DuckDBHistoricalCandleStore):
        raise RepairError('LOCAL_DUCKDB_REQUIRED')
    if not isinstance(secid, str) or not re.fullmatch(r'BR[FGHJKMNQUVXZ][0-9]', secid) or timeframe not in {'H1', 'M15'}:
        raise RepairError('EXACT_BR_CONTRACT_REQUIRED')
    start, finish = date.fromisoformat(from_date), date.fromisoformat(till_date)
    if start.isoformat() != from_date or finish.isoformat() != till_date or not 0 <= (finish-start).days < 367:
        raise RepairError('INVALID_BOUNDED_DATE_RANGE')
    with store._lock:
        if not table_exists(store._connection):
            rows = []
        else:
            rows = store._connection.execute('''SELECT payload_json,dataset_sha256,imported_at,payload_sha256
                FROM br_research_quarantine WHERE secid=? AND timeframe=? AND begin>=? AND begin<?
                ORDER BY begin LIMIT 5001''', [secid, timeframe, from_date, (finish+timedelta(days=1)).isoformat()]).fetchall()
    if len(rows) > MAX_ROWS:
        raise RepairError('ROW_BOUND_EXCEEDED')
    for payload, _, _, expected in rows:
        if digest(json.loads(payload)) != expected:
            raise RepairError('STORED_RESEARCH_SHA_MISMATCH')
    return {'status': 'RESEARCH_ONLY', 'version': VERSION, 'secid': secid,
            'timeframe': timeframe, 'count': len(rows), 'forecast_admission': 'NOT_GRANTED',
            'calendar_status': 'UNVERIFIED', 'historical_availability_status': 'UNVERIFIED',
            'records': [{'source_record': json.loads(p), 'dataset_sha256': ds,
                         'imported_at': t, 'imported_at_is_historical_first_seen': False} for p, ds, t, _ in rows]}
