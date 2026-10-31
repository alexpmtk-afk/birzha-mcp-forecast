"""A real shared archive pass on synthetic exact-contract Google transport."""
from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
import threading
import pytest
from test_model3_archive import Book, candles
from birzha.application.archive_sync import ArchiveSync
from birzha.application.model3_archive import Model3ArchiveUpdater
from birzha.application.model3_features import COLUMNS, LEGACY_COLUMNS
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.application.continuous_series import build_continuous_series
from birzha.application.forts_calendar_2026 import trade_day,kind
from test_continuous_series import source


def fixture(tmp_path):
    b=Book(n=0)
    for tf in ('D1','H1','M15'):
        h=b.data[tf][0];rows=[]
        for day in ('2026-03-18','2026-03-19'):
            for sec,price in [('RIH6',100),('RIM6',110)]:
                r=dict(zip(h,candles(tf,1,sec)[1]))
                begin=day+'T10:00:00+03:00'
                r.update(record_key=f'RI|{tf}|{sec}|{begin}',calendar_date=day,
                    bar_start_time=begin,bar_end_time=day+'T10:14:59+03:00',
                    open=price,high=price+2,low=price-2,close=price,volume=50)
                rows.append([r.get(k,'') for k in h])
        b.data[tf]=[h,*rows]
    class Resolver:
        calls=0
        def _previous_observed_session(self,root,day):
            self.calls+=1;prior=day-timedelta(days=1)
            return prior,[dict(SECID=sec,ASSETCODE='RI',TRADEDATE=prior.isoformat(),
                LASTTRADEDATE='2026-03-19' if sec=='RIH6' else '2026-06-18',
                VOLUME=100 if (sec=='RIH6')==(day==date(2026,3,18)) else 50,
                CLOSE=price) for sec,price in [('RIH6',100),('RIM6',110)]]
    market=SimpleNamespace(historical_future_resolver=Resolver())
    updater=Model3ArchiveUpdater(b,audit_dir=str(tmp_path))
    svc=ArchiveSync(b,market,updater,str(tmp_path),threading.Lock())
    return b,market,svc


def call(svc,**kw):
    return svc.sync(symbol='RI',from_date='2026-03-18',till_date='2026-03-19',**kw)


def test_one_command_shared_ledger_prices_volume_raw_and_repeat(tmp_path):
    b,m,svc=fixture(tmp_path);raw=deepcopy(b.data)
    assert call(svc)['network_requests']==0
    assert m.historical_future_resolver.calls==0 and not b.writes
    result=call(svc,dry_run=False,exclusive_workbook_owner=True)
    assert result['status']=='ARCHIVE_SYNC_READBACK_RESEARCH_ONLY'
    assert result['source_windows']==0 and not result['forecast_allowed']
    assert result['roll_events'][0]['basis']==10
    assert m.historical_future_resolver.calls==2
    assert result['full_source_coverage']=='NOT_PROVEN'
    for tf in ('D1','H1','M15'):
        assert b.data[tf]==raw[tf]
        rows=b.data[tf+'_INDICATORS'];h=rows[0]
        selected=[r for r in rows[1:] if r[h.index('continuous_selected')]]
        assert len(selected)==2
        assert {r[h.index('continuous_close')] for r in selected}=={100}
        assert {r[h.index('secid')] for r in selected}=={'RIH6','RIM6'}
        assert all(r[h.index('continuous_close')]=='' for r in rows[1:] if not r[h.index('continuous_selected')])
    before=deepcopy(b.data);writes=len(b.writes)
    call(svc,dry_run=False,exclusive_workbook_owner=True)
    assert b.data==before and len(b.writes)==writes
    assert m.historical_future_resolver.calls==2


def test_partial_budget_is_resumable_and_does_not_claim_continuous(tmp_path):
    b,m,svc=fixture(tmp_path)
    # Every selected frame on the second day is missing; no provider is spent
    # once the caller's bounded source budget is exhausted.
    from birzha.application.archive_collection import ArchiveCollector
    original=ArchiveCollector.collect
    calls=[]
    def fake(self,**kw):
        calls.append(kw)
        tf=kw['timeframe'];h=b.data[tf][0]
        r=dict(zip(h,candles(tf,1,'RIM6')[1]))
        begin='2026-03-19T10:00:00+03:00'
        r.update(record_key=f'RI|{tf}|RIM6|{begin}',bar_start_time=begin,
            bar_end_time='2026-03-19T10:14:59+03:00')
        b.data[tf].append([r.get(k,'') for k in h])
        return dict(source_rows=1)
    for tf in ('D1','H1','M15'):
        h=b.data[tf][0]
        b.data[tf]=[h,*[r for r in b.data[tf][1:] if not (r[8]=='RIM6' and r[4].startswith('2026-03-19'))]]
    from unittest.mock import patch
    with patch.object(ArchiveCollector,'collect',fake):
        result=call(svc,dry_run=False,exclusive_workbook_owner=True,max_source_windows=1)
    assert result['status']=='PARTIAL_SOURCE_BUDGET' and not result['continuous_written']
    assert len(calls)==1 and b.status['archive_sync_status']=='PARTIAL_RESUME_SAME_ARGUMENTS'


def test_lost_write_response_requires_reconciliation_not_blind_retry(tmp_path):
    b,m,svc=fixture(tmp_path);b.lose_once=True
    with pytest.raises(RuntimeError,match='lost'):call(svc,dry_run=False,exclusive_workbook_owner=True)
    assert b.status['archive_sync_status']=='FAILED_RECONCILE_BEFORE_RETRY'
    call(svc,dry_run=False,exclusive_workbook_owner=True)
    for tf in ('D1','H1','M15'):
        assert len(b.data[tf+'_INDICATORS'])==5


def test_metadata_cache_tamper_and_calendar_conflict_block_coordinates(tmp_path):
    b,m,svc=fixture(tmp_path)
    call(svc,dry_run=False,exclusive_workbook_owner=True)
    key='continuous_decision|2026-03-19'
    b.status[key]=b.status[key].replace('"CLOSE":110','"CLOSE":999')
    with pytest.raises(MirrorConflict,match='cache hash'):call(svc,dry_run=False,exclusive_workbook_owner=True)


def test_unknown_availability_never_promoted_to_strict():
    x=source()
    for s in x['sessions']:
        for r in s['decision_rows']:r['available_at']=None
    with pytest.raises(ValueError,match='unknown'):build_continuous_series(x)
    x['evidence_mode']='RECONSTRUCTED_RESEARCH'
    assert not build_continuous_series(x)['forecast_allowed']


def test_documented_2026_weekends_extra_holiday_and_unknown_year():
    assert trade_day(date(2026,5,1))==date(2026,5,4)
    assert trade_day(date(2026,5,2))==date(2026,5,4)
    assert trade_day(date(2026,6,12))==date(2026,6,15)
    assert kind(date(2026,10,24))=='CLOSED'
    with pytest.raises(ValueError):trade_day(date(2027,1,4))


def test_prefix_ledger_does_not_require_unrequested_intraday_warmup():
    x=source();x['bars']=x['bars'][3:]
    with pytest.raises(ValueError,match='missing selected'):build_continuous_series(x)
    x['required_sessions']=['2026-03-19']
    assert len(build_continuous_series(x)['bars'])==3
    x['required_sessions']=['2026-03-20']
    with pytest.raises(ValueError,match='subset'):build_continuous_series(x)


def test_legacy_109_columns_migrate_without_replacing_existing_cells(tmp_path):
    b,m,svc=fixture(tmp_path)
    for tf in ('D1','H1','M15'):b.data[tf+'_INDICATORS']=[LEGACY_COLUMNS[:]]
    call(svc,dry_run=False,exclusive_workbook_owner=True)
    assert all(b.data[tf+'_INDICATORS'][0]==COLUMNS for tf in ('D1','H1','M15'))


def test_real_stdio_archive_sync_command_executes_full_cycle(tmp_path):
    import asyncio,os,sys
    from pathlib import Path
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    root=Path(__file__).resolve().parents[1]
    runner=tmp_path/'sync_stdio.py'
    runner.write_text(
        "import sys,dataclasses,socket\n"
        f"sys.path.insert(0,{str(root/'tests')!r})\n"
        "from pathlib import Path\n"
        "from test_archive_sync import fixture\n"
        "from types import SimpleNamespace\n"
        "from birzha.mcp import server\n"
        "def forbidden(*a,**kw):raise AssertionError('external network forbidden')\n"
        "import httpx\nhttpx.Client.request=forbidden\n"
        f"b,m,svc=fixture(Path({str(tmp_path/'audit')!r}))\n"
        "server._model3_archive=svc.updater\nserver._market=m\n"
        "server._native_mirror=SimpleNamespace(transport=b,_lock=svc.lock)\n"
        f"server.settings=dataclasses.replace(server.settings,native_mirror_sole_writer=True,model3_audit_dir={str(tmp_path/'audit')!r})\n"
        "server.mcp.run(transport='stdio')\n",encoding='utf8')
    env={**os.environ,'PYTHONPATH':str(root/'src'),'PYTHONIOENCODING':'utf-8',
        'BIRZHA_STATE_BACKEND':'duckdb','BIRZHA_FORECAST_JOURNAL_PATH':':memory:',
        'BIRZHA_HISTORICAL_STORE_PATH':':memory:','BIRZHA_NATIVE_MIRROR_ENABLED':'false',
        'BIRZHA_REQUIRE_MCP_AUTH':'false'}
    async def check():
        async with stdio_client(StdioServerParameters(command=sys.executable,args=[str(runner)],env=env)) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                assert 'archive.sync' in {t.name for t in (await client.list_tools()).tools}
                args=dict(symbol='RI',from_date='2026-03-18',till_date='2026-03-19')
                for dry,status in [(True,'ARCHIVE_SYNC_PLANNED'),(False,'ARCHIVE_SYNC_READBACK_RESEARCH_ONLY'),(False,'ARCHIVE_SYNC_READBACK_RESEARCH_ONLY')]:
                    reply=await client.call_tool('archive.sync',arguments={**args,'dry_run':dry})
                    assert not reply.is_error
                    value=reply.structured_content
                    assert value['status']==status and not value['forecast_allowed']
    asyncio.run(asyncio.wait_for(check(),timeout=45))


def test_patch_windows_are_bounded_and_preserve_unrelated_values(tmp_path):
    b,m,svc=fixture(tmp_path)
    call(svc,dry_run=False,exclusive_workbook_owner=True)
    windows=[]
    def read_window(symbol,sheet,start,stop,width):
        windows.append((start,stop))
        data=b.read_rows(symbol,sheet)
        return [r+['']*(width-len(r)) for r in data[start:stop]]+[['']*width for _ in range(max(0,stop-max(start,len(data))))]
    b.model3_read_window=read_window
    sheet='H1_INDICATORS';before=deepcopy(b.data[sheet]);after=deepcopy(before)
    after[2][COLUMNS.index('continuous_close')]=123
    svc.updater._publish(sheet,before,after)
    assert b.data[sheet]==after
    assert windows and max(stop-start for start,stop in windows)<=2000


def test_native_window_uses_requested_rows_and_pads_schema_growth():
    import httpx
    from urllib.parse import unquote
    from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
    calls=[]
    def handler(request):
        calls.append(unquote(str(request.url)))
        assert request.url.params['valueRenderOption']=='FORMULA'
        return httpx.Response(200,json={'values':[['a',1],['b']]})
    native=NativeCanonicalSheets(lambda:'dummy',transport=httpx.MockTransport(handler))
    native._sheets[('RI','H1_INDICATORS')]={'gridProperties':{'columnCount':109,'rowCount':20}}
    rows=native.model3_read_window('RI','H1_INDICATORS',8,11,119)
    assert len(rows)==3 and all(len(r)==119 for r in rows)
    assert rows[0][:2]==['a',1] and rows[2]==['']*119
    assert "'H1_INDICATORS'!A9:DE11" in calls[0]


def test_m15_refuses_source_download_without_persistent_minute_capture(tmp_path):
    from test_archive_collection import collector,call as collect_call
    from birzha.application.archive_collection import ArchiveCollector
    b,m,svc=collector(tmp_path)
    b.data['M15']=[b.data['M15'][0]]
    with pytest.raises(MirrorConflict,match='persistent RI M1'):
        svc.collect(symbol='RI',exact_secid='RIZ6',timeframe='M15',
            from_date='2026-10-29',till_date='2026-10-29',dry_run=False,exclusive_workbook_owner=True)
    assert m.calls==0


def test_recorded_empty_source_does_not_block_other_missing_windows(tmp_path):
    from birzha.application.canonical_market_mirror import digest
    import json
    b,m,svc=fixture(tmp_path)
    for tf in ('D1','H1','M15'):
        h=b.data[tf][0]
        b.data[tf]=[h,*[r for r in b.data[tf][1:] if not (r[8]=='RIM6' and r[4].startswith('2026-03-19'))]]
        b.status[f'archive_source|RIM6|{tf}|2026-03-19|2026-03-19']=json.dumps(dict(rows_sha256=digest([]),source_rows=0))
    result=call(svc,dry_run=False,exclusive_workbook_owner=True,max_source_windows=1)
    assert result['status']=='PARTIAL_SOURCE_UNAVAILABLE'
    assert result['source_windows']==0 and len(result['unavailable'])==3
    assert all(len(b.data[tf])==4 for tf in ('D1','H1','M15'))


def test_modified_google_source_invalidates_source_receipt(tmp_path):
    from birzha.application.canonical_market_mirror import digest
    import json
    b,m,svc=fixture(tmp_path)
    tf='H1';rows=[r for r in b.data[tf][1:] if r[8]=='RIH6' and r[4].startswith('2026-03-18')]
    b.status['archive_source|RIH6|H1|2026-03-18|2026-03-18']=json.dumps(dict(rows_sha256=digest(rows),source_rows=1))
    rows[0][13]+=1
    with pytest.raises(MirrorConflict,match='receipt no longer matches'):
        call(svc,dry_run=False,exclusive_workbook_owner=True)
