from copy import deepcopy
from pathlib import Path
import pytest
from birzha.application.br_research_repair import (
    reconcile, research_series, digest, RepairError, table_exists, fingerprint)
from birzha.storage.historical_store import DuckDBHistoricalCandleStore
from birzha.domain.market import Instrument, Candle, CandleSeries


def dataset():
    return {'status':'PROPOSAL_ONLY_NOT_APPROVED_FOR_IMPORT',
        'source_book':'1y8nMiqmMKv1af_lRQjGPCYDKpz3P1nfojkPjqqa4JjQ', 'count':1,
        'records':[{'timeframe':'H1','record_quality':'RESEARCH_NOT_ADMITTED',
        'forecast_admission':'NOT_GRANTED','sheet_row':{'contract_code':'BRZ5',
        'bar_start_time':'2025-11-01T08:00:00+03:00','bar_end_time':'2025-11-01T08:59:59+03:00',
        'open':10.0,'high':12.0,'low':9.0,'close':11.0,'volume':20.0,
        'is_complete':True,'available_at':'2025-11-01T08:59:59+03:00'}}]}


def instrument():
    return Instrument(symbol='BR',secid='BRZ5',board='RFUD',engine='futures',market='forts',asset_class='future',root_symbol='BR')


def test_dry_run_has_no_schema_or_data_writes():
    s=DuckDBHistoricalCandleStore(':memory:');p=dataset()
    try:
        tables=s._connection.execute('SHOW TABLES').fetchall()
        r=reconcile(s,p,digest(p))
        assert r['would_insert']==1 and r['inserted']==0
        assert tables==s._connection.execute('SHOW TABLES').fetchall()
        assert not table_exists(s._connection)
    finally:s.close()


def test_apply_and_repeat_preserve_forecast_reader_and_original_payload():
    s=DuckDBHistoricalCandleStore(':memory:');p=dataset()
    try:
        before=fingerprint(s._connection)
        r=reconcile(s,p,digest(p),apply=True)
        assert r['inserted']==1 and before==fingerprint(s._connection)
        assert s.coverage('BRZ5','H1').count==0
        assert s.read(instrument(),'H1','2025-11-01','2025-11-01').candles==()
        q=research_series(s,'BRZ5','H1','2025-11-01','2025-11-01')
        assert q['count']==1 and q['forecast_admission']=='NOT_GRANTED'
        assert q['records'][0]['source_record']==p['records'][0]
        assert q['records'][0]['imported_at_is_historical_first_seen'] is False
        assert reconcile(s,p,digest(p),apply=True)['already_research']==1
    finally:s.close()


@pytest.mark.parametrize('change,reason',[
    (lambda p:p.update(source_book='another'),'INVALID_BR_DATASET'),
    (lambda p:p.update(count=2),'INVALID_BR_DATASET'),
    (lambda p:p['records'][0].update(forecast_admission='PASS'),'INVALID_RESEARCH_IDENTITY_OR_ADMISSION'),
    (lambda p:p['records'][0]['sheet_row'].update(contract_code='GDZ5'),'INVALID_RESEARCH_IDENTITY_OR_ADMISSION'),
    (lambda p:p['records'][0]['sheet_row'].update(low=100),'INVALID_OHLCV'),
    (lambda p:p['records'][0]['sheet_row'].update(volume=-1),'INVALID_OHLCV'),
    (lambda p:p['records'][0]['sheet_row'].update(open=float('inf')),None),
    (lambda p:p['records'][0]['sheet_row'].update(bar_end_time='2025-10-31T00:00:00'),'INVALID_RESEARCH_INTERVAL'),
])
def test_negative_validation_never_creates_quarantine(change,reason):
    s=DuckDBHistoricalCandleStore(':memory:');p=dataset();change(p)
    try:
        with pytest.raises(ValueError,match=reason):reconcile(s,p,digest(p),apply=True)
        assert not table_exists(s._connection)
    finally:s.close()


def test_sha_and_duplicate_guards():
    s=DuckDBHistoricalCandleStore(':memory:');p=dataset()
    try:
        with pytest.raises(RepairError,match='SHA_MISMATCH'):reconcile(s,p,'0'*64,apply=True)
        p['records'].append(deepcopy(p['records'][0]));p['count']=2
        with pytest.raises(RepairError,match='DUPLICATE'):reconcile(s,p,digest(p),apply=True)
    finally:s.close()


def test_m15_cannot_be_an_hour_and_apply_preserves_other_instrument():
    s=DuckDBHistoricalCandleStore(':memory:');p=dataset()
    try:
        other=Instrument(symbol='SBER',secid='SBER',board='TQBR',engine='stock',market='shares',asset_class='stock')
        c=Candle(open=10.,close=11.,high=12.,low=9.,volume=20.,value=None,
            begin='2025-11-01 08:00:00',end='2025-11-01 08:59:59',completed=True)
        s.upsert_series(CandleSeries(other,'H1',(c,)))
        before=fingerprint(s._connection)
        p['records'][0]['timeframe']='M15'
        with pytest.raises(RepairError,match='INVALID_RESEARCH_INTERVAL'):
            reconcile(s,p,digest(p),apply=True)
        assert fingerprint(s._connection)==before and not table_exists(s._connection)
        p['records'][0]['sheet_row']['bar_end_time']='2025-11-01T08:14:59+03:00'
        assert reconcile(s,p,digest(p),apply=True)['inserted']==1
        assert fingerprint(s._connection)==before
    finally:s.close()


def test_existing_accepted_conflict_rolls_back_and_equal_is_skipped():
    s=DuckDBHistoricalCandleStore(':memory:');p=dataset()
    try:
        c=Candle(open=10.,close=11.,high=12.,low=9.,volume=20.,value=None,
            begin='2025-11-01 08:00:00',end='2025-11-01 08:59:59',completed=True)
        s.upsert_series(CandleSeries(instrument(),'H1',(c,)))
        before=fingerprint(s._connection)
        assert reconcile(s,p,digest(p),apply=True)['already_accepted']==1
        p['records'][0]['sheet_row']['close']=10.5
        with pytest.raises(RepairError,match='ACCEPTED_CANDLE_CONFLICT'):reconcile(s,p,digest(p),apply=True)
        assert before==fingerprint(s._connection) and not table_exists(s._connection)
    finally:s.close()


def test_research_conflict_and_future_native_loading():
    s=DuckDBHistoricalCandleStore(':memory:');p=dataset()
    try:
        reconcile(s,p,digest(p),apply=True)
        other=deepcopy(p);other['records'][0]['sheet_row']['close']=10.5
        with pytest.raises(RepairError,match='RESEARCH_CANDLE_CONFLICT'):reconcile(s,other,digest(other),apply=True)
        c=Candle(open=10.,close=11.,high=12.,low=9.,volume=20.,value=None,
            begin='2025-11-01 08:00:00',end='2025-11-01 08:59:59',completed=True)
        s.upsert_series(CandleSeries(instrument(),'H1',(c,)))
        assert s.coverage('BRZ5','H1').count==1
        assert reconcile(s,p,digest(p),apply=True)['already_accepted']==1
    finally:s.close()


def test_research_reader_rejects_stored_payload_corruption():
    s=DuckDBHistoricalCandleStore(':memory:');p=dataset()
    try:
        reconcile(s,p,digest(p),apply=True)
        altered=deepcopy(p['records'][0]);altered['sheet_row']['close']=10.5
        from birzha.application.br_research_repair import canonical
        s._connection.execute('UPDATE br_research_quarantine SET payload_json=?',[canonical(altered)])
        with pytest.raises(RepairError,match='STORED_RESEARCH_SHA_MISMATCH'):
            research_series(s,'BRZ5','H1','2025-11-01','2025-11-01')
    finally:s.close()


def test_file_backup_precedes_quarantine_and_can_restore(tmp_path):
    s=DuckDBHistoricalCandleStore(str(tmp_path/'history.duckdb'));p=dataset()
    try:
        r=reconcile(s,p,digest(p),apply=True)
        backup=Path(r['backup_directory'])
        assert (backup/'load.sql').is_file() and (backup/'schema.sql').is_file()
        import duckdb
        con=duckdb.connect(':memory:')
        con.execute("IMPORT DATABASE '"+str(backup).replace("'","''")+"'")
        assert not table_exists(con)
        assert con.execute('SELECT count(*) FROM historical_candles').fetchone()[0]==0
        con.close()
    finally:s.close()


@pytest.mark.parametrize('secid,tf,start,till',[
    ('BR','H1','2025-01-01','2025-01-01'),
    ('BRZ5','D1','2025-01-01','2025-01-01'),
    ('BRZ5','H1','2025-01-01','2026-12-31'),
])
def test_read_bounds(secid,tf,start,till):
    s=DuckDBHistoricalCandleStore(':memory:')
    try:
        with pytest.raises(RepairError):research_series(s,secid,tf,start,till)
    finally:s.close()
