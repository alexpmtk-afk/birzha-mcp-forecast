"""Bounded read-only MOEX ISS provenance probe; never builds or journals forecasts.

Uses the EXISTING MoexIssClient._request, which enforces the shared public ISS
request governor. Captures ORIGINAL response bytes, observed UTC wall time, and
canonical URI parameters for a fixed SBER D1 endpoint. Staging scratch only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
from typing import Callable, Any
from zoneinfo import ZoneInfo
import argparse

TZ = ZoneInfo('Europe/Moscow')
VERSION = 'G2_MOEX_RAW_SBER_D1_CAPTURE_PROBE_V1'
ISS_PATH = '/engines/stock/markets/shares/boards/TQBR/securities/SBER/candles.json'
COLUMNS = 'open,close,high,low,value,volume,begin,end'


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _moex_local(value: str) -> datetime:
    result = datetime.fromisoformat(str(value).replace(' ', 'T'))
    if result.tzinfo is None:
        result = result.replace(tzinfo=TZ)
    return result.astimezone(timezone.utc)


def _utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('wall clock must be timezone aware')
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def capture(provider: Any, output: Path, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    """One governed request; a failed validation NEVER claims successful capture."""
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('refusing non-empty capture directory overwrite')
    before = clock().astimezone(timezone.utc)
    if before.utcoffset() is None:
        raise ValueError('wall clock must be timezone aware')
    until = before.astimezone(TZ).date() - timedelta(days=1)
    start = until - timedelta(days=13)
    params = {'iss.meta':'off','iss.only':'candles','from':start.isoformat(),
              'till':until.isoformat(),'interval':24,'candles.columns':COLUMNS,'start':0}
    # This method is governed by MoexIssClient's policy; no raw unmetered HTTP.
    response = provider._request(ISS_PATH, params)
    after = clock().astimezone(timezone.utc)
    if after < before or (after-before).total_seconds() > 90:
        raise ValueError('clock reversed or provider request took >90 seconds')
    if response.status_code != 200 or not isinstance(response.body, bytes) or not response.body:
        raise ValueError('no valid response body from governed MOEX client')
    payload = json.loads(response.body.decode('utf8'))
    table = payload.get('candles') or {}
    cols = table.get('columns') or []
    data = table.get('data') or []
    if not data or any(key not in cols for key in ('begin','end','close')):
        raise ValueError('MOEX candle response lacks required D1 data')
    cursor = payload.get('candles.cursor') or {}
    if cursor.get('data'):
        c_cols = cursor.get('columns') or []
        c = dict(zip(c_cols,cursor['data'][0]))
        if c.get('TOTAL') is not None and int(c['TOTAL']) > len(data):
            raise ValueError('captured response is paginated/incomplete')
    rows = [dict(zip(cols,r)) for r in data]
    ends=[]
    for item in rows:
        begin,end = _moex_local(item['begin']),_moex_local(item['end'])
        if begin >= end or end > before:
            raise ValueError('forming or future-dated D1 candle: refuse entire payload')
        close = float(item['close'])
        if not 0 < close < float('inf'):
            raise ValueError('invalid D1 close')
        if end.astimezone(TZ).date() > until:
            raise ValueError('response outside pinned date window')
        ends.append(end)
    if tuple(sorted(set(ends))) != tuple(ends):
        raise ValueError('response has non-monotonic/duplicate candle end')
    header_date = response.headers.get('date') or response.headers.get('Date')
    if header_date:
        header_date = _utc(parsedate_to_datetime(header_date))
    receipt = {
        'version':VERSION,'market':'SBER','secid':'SBER','timeframe':'D1',
        'provider_host':'iss.moex.com','provider_path':ISS_PATH,
        'request_params':params,'observed_at_start_utc':_utc(before),
        'observed_at_end_utc':_utc(after),'latest_completed_end_utc':_utc(ends[-1]),
        'http_status':200,'http_date_header_utc':header_date,
        'raw_sha256':hashlib.sha256(response.body).hexdigest(),
        'raw_byte_count':len(response.body),'completed_rows_in_response':len(rows),
        'record_type':'RAW_PUBLIC_RESPONSE_ONLY','is_real_forecast':False,
        'strict_pit_proven':False,'independent_clock_attestation':False,
        'notes':['One provider-governed request, no forecast issued',
                 'Local UTC receipt clock is not a third-party trusted timestamp',
                 'Does not certify historical versions or future market outcomes'],
    }
    output.mkdir(parents=True,exist_ok=True)
    raw_path = output/'moex_sber_d1_raw_response.json'
    receipt_path = output/'moex_sber_d1_receipt.json'
    # Exclusive writes: never replace an existing snapshot.
    with raw_path.open('xb') as f:
        f.write(response.body)
    with receipt_path.open('x',encoding='utf8') as f:
        f.write(_canonical(receipt)+'\n')
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    args = parser.parse_args()
    from birzha.providers.moex_iss import MoexIssClient
    from birzha.application.upstream_control import ProcessUpstreamControlPlane
    provider=MoexIssClient(control_plane=ProcessUpstreamControlPlane())
    receipt=capture(provider,args.output_dir)
    print(_canonical({'version':VERSION,'sha256':receipt['raw_sha256'],
                      'completed':receipt['completed_rows_in_response'],
                      'first_receipt':receipt['observed_at_end_utc'],
                      'is_real_forecast':False}))


if __name__ == '__main__':main()
