"""SYNTHETIC / ISOLATED demonstration of first capture and 5/10/20 outcomes.

This is a contract rehearsal, NOT a live market-data capture or real forecast.
It cannot be used to infer model skill or historical causal validity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone, date
import hashlib
import json
from pathlib import Path

from birzha.application.prospective_capture import (
    ProspectivePilotLedger, CaptureEvidence, CompletedSessions,
)


def _stable(x):
    return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))


class _SyntheticForecast:
    def to_dict(self):
        return {
          'record_version':'FORECAST_RECORD_V1_PROTOCOL_08',
          'forecast_id':'SYNTHETIC_ONLY_SBER_20261010','symbol':'SBER','secid':'SBER',
          'snapshot_id':'snap_synthetic_fixture_not_live',
          'snapshot_contract_version':'MARKET_SNAPSHOT_V2',
          'engine_version':'G2_SYNTHETIC_PILOT_ONLY',
          'created_at_t0':'2026-10-10T11:59:30Z',
          'reference_price':100.0,
          'horizons':[{'sessions':h,'direction':'UP','signal_strength':0.1,'expected_move_pct':None,'adverse_move_pct':None} for h in (5,10,20)],
          'reasons':['synthetic fixture, not an observed market prediction'],
          'validation_status':'SYNTHETIC_NON_PRODUCTION',
        }


def _session_dates():
    days=[];d=date(2026,10,11)
    while len(days)<20:
        d+=timedelta(days=1)
        if d.weekday()<5:days.append(d.isoformat())
    return days


def demo(output_dir:Path):
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('use a fresh isolated output directory; refusing overwrite')
    output_dir.mkdir(parents=True,exist_ok=True)
    now=[datetime(2026,10,10,12,0,tzinfo=timezone.utc)]
    ledger=ProspectivePilotLedger(output_dir/'synthetic_pilot.sqlite3',clock=lambda:now[0])
    fixture=b'{"fixture":"synthetic","price":100.0,"as_of":"2026-10-10"}'
    captured=ledger.capture(_SyntheticForecast(),CaptureEvidence(
        source_payload=fixture,source_observed_at='2026-10-10T11:59:25Z',
        latest_completed_event_end='2026-10-09T20:00:00Z'))
    receipt=captured['receipt']
    ledger.close()
    dates=_session_dates()
    outcomes=[]
    for h in (5,10,20):
        subset=dates[:h]
        observed=f'{subset[-1]}T22:10:00+03:00'
        now[0]=datetime.fromisoformat(f'{subset[-1]}T23:00:00+03:00')
        ledger=ProspectivePilotLedger(output_dir/'synthetic_pilot.sqlite3',clock=lambda:now[0])
        future_fixture=_stable({'fixture':'synthetic','sessions':subset,'closes':[float(100+i) for i in range(1,h+1)]}).encode()
        result=ledger.observe(receipt['forecast_id'],h,CompletedSessions(
            source_payload=future_fixture,source_observed_at=observed,
            market='SBER',secid='SBER',session_dates=tuple(subset),
            expected_calendar_dates=tuple(subset),
            candle_completed_at=tuple(f'{day}T21:00:00+03:00' for day in subset),
            candle_close=tuple(float(100+i) for i in range(1,h+1)),
        ))
        assert result['status']=='APPENDED'
        outcomes.append(result['outcome'])
        ledger.close()
    ledger=ProspectivePilotLedger(output_dir/'synthetic_pilot.sqlite3',clock=lambda:now[0])
    audit=ledger.audit()
    ledger.close()
    result={'experiment':'SYNTHETIC_ONLY_PROSPECTIVE_RECEIPT_TEST',
        'NOT_REAL_FORECAST':True,'strict_historical_PIT':False,
        'receipt':receipt,'outcomes':outcomes,'audit':audit,
        'sql_ledger_sha256':hashlib.sha256((output_dir/'synthetic_pilot.sqlite3').read_bytes()).hexdigest()}
    (output_dir/'synthetic_pilot_report.json').write_text(_stable(result)+'\n',encoding='utf8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args()
    print(_stable(demo(args.output_dir)['audit']))


if __name__=='__main__':main()
