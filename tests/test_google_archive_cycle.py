import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from scripts import run_google_archive_cycle as client

def test_scheduled_d1_scans_policy_start_even_with_fresh_last_date(monkeypatch):
    """A fresh 2026 candle must not hide an interior 2025 gap."""
    calls=[]
    class Session:
        def __init__(self,*a,**kw):assert kw['read_timeout_seconds']==1800
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def initialize(self):pass
        async def call_tool(self,name,args):
            calls.append((name,args))
            if name=='archive.status':
                v={'runtime':'GOOGLE_FIRST','local_candle_store':False,'source_sha':'a'*40,'sole_writer':True}
            elif name=='archive.coverage':
                v={'timeframes':{'D1':{'last_date':'2026-10-29','archive_start':'2025-01-01','scan_cursor':'2025-01-01'}}}
            elif name=='archive.sync':v={'status':'PASS','results':[{'status':'READBACK_PASS'}]}
            else:raise AssertionError('unexpected tool')
            return SimpleNamespace(is_error=False,structured_content=v)
    @asynccontextmanager
    async def streams(*a,**k):yield (None,None)
    monkeypatch.setattr(client,'ClientSession',Session)
    monkeypatch.setattr(client,'streamable_http_client',streams)
    monkeypatch.setenv('BIRZHA_SOURCE_SHA','a'*40)
    r=asyncio.run(client.cycle('http://127.0.0.1:18085/mcp','isolated-test-token',
        from_date=None,till_date='2026-10-30',timeframes=['D1'],dry_run=False,scan_days_per_run=3,tail_days=0))
    writes=[a for n,a in calls if n=='archive.sync']
    assert r['status']=='PASS' and len(writes)==6
    assert [a['symbol'] for a in writes]==['RI','Si','BR','GOLD','SBER','IMOEX']
    assert all(a['from_date']=='2025-01-01' and a['till_date']=='2025-01-03'
               and a['timeframes']==['D1'] and a['scan_mode'] is True for a in writes)


def test_managed_scan_uses_cloud_cursor_on_next_cycle(monkeypatch):
    cursors={s:'2025-01-01' for s in ('RI','Si','BR','GOLD','SBER','IMOEX')}
    seen=[]
    class Session:
        def __init__(self,*a,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def initialize(self):pass
        async def call_tool(self,name,args):
            if name=='archive.status':
                value={'runtime':'GOOGLE_FIRST','local_candle_store':False,'source_sha':'a'*40,'sole_writer':True}
            elif name=='archive.coverage':
                value={'timeframes':{'D1':{'last_date':'2026-10-29','archive_start':'2025-01-01',
                                          'scan_cursor':cursors[args['symbol']]}}}
            elif name=='archive.sync':
                assert args['scan_mode'] is True and args['timeframes']==['D1']
                seen.append((args['symbol'],args['from_date'],args['till_date']))
                from datetime import date,timedelta
                cursors[args['symbol']]=(date.fromisoformat(args['till_date'])+timedelta(days=1)).isoformat()
                value={'status':'PASS','results':[{'status':'READBACK_PASS'}]}
            else:raise AssertionError(name)
            return SimpleNamespace(is_error=False,structured_content=value)
    @asynccontextmanager
    async def streams(*a,**k):yield (None,None)
    monkeypatch.setattr(client,'ClientSession',Session)
    monkeypatch.setattr(client,'streamable_http_client',streams)
    monkeypatch.setenv('BIRZHA_SOURCE_SHA','a'*40)
    for _ in range(2):
        result=asyncio.run(client.cycle('http://127.0.0.1:18085/mcp','test-token',
            from_date=None,till_date='2026-10-30',timeframes=['D1'],dry_run=False,scan_days_per_run=3,tail_days=0))
        assert result['status']=='PASS'
    assert [x[1:] for x in seen if x[0]=='RI']==[
        ('2025-01-01','2025-01-03'),('2025-01-04','2025-01-06')]
    assert all(cursor=='2025-01-07' for cursor in cursors.values())


def test_scheduler_fail_closed_without_cloud_cursor(monkeypatch):
    seen=[]
    class Session:
        def __init__(self,*a,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def initialize(self):pass
        async def call_tool(self,name,args):
            seen.append(name)
            value=({'runtime':'GOOGLE_FIRST','local_candle_store':False,'source_sha':'a'*40,'sole_writer':True}
                if name=='archive.status' else {'timeframes':{'D1':{'last_date':'2026-10-29'}}})
            assert name!='archive.sync'
            return SimpleNamespace(is_error=False,structured_content=value)
    @asynccontextmanager
    async def streams(*a,**k):yield (None,None)
    monkeypatch.setattr(client,'ClientSession',Session)
    monkeypatch.setattr(client,'streamable_http_client',streams)
    monkeypatch.setenv('BIRZHA_SOURCE_SHA','a'*40)
    result=asyncio.run(client.cycle('http://127.0.0.1:18085/mcp','test-token',
        from_date=None,till_date='2026-10-30',timeframes=['D1'],dry_run=False,scan_days_per_run=3,tail_days=0))
    assert result['status']=='PARTIAL'
    assert all(x['status']=='CLOUD_SCAN_CURSOR_REQUIRED' for x in result['results'])
    assert 'archive.sync' not in seen


@pytest.mark.parametrize('url',['https://example.org/mcp','http://127.0.0.1:18085/wrong','http://user:pass@127.0.0.1:18085/mcp'])
def test_scheduler_rejects_unapproved_endpoint(url):
    with pytest.raises(ValueError,match='LOOPBACK'):asyncio.run(client.cycle(url,'isolated-test-token',from_date=None,till_date='2026-10-30',timeframes=['D1'],dry_run=True))


@pytest.mark.parametrize("window_status,child_status,windows_per_symbol", [
    ("PARTIAL", "SOURCE_NOT_AVAILABLE", 2),
    ("PARTIAL", "SOURCE_NOT_AVAILABLE_RETRY_LATER", 2),
    ("PARTIAL", "EXISTING_UNVERIFIED_NO_FETCH", 2),
    ("BLOCKED", "ERROR_RECONCILE_BEFORE_RETRY", 1),
    ("PARTIAL", "UNRECOGNIZED_RESULT", 1),
    ("PARTIAL_WITH_CURSOR", "SOURCE_NOT_AVAILABLE", 1),
])
def test_scheduler_reaches_later_dates_after_safe_partial_windows(monkeypatch, window_status, child_status, windows_per_symbol):
    """A closed/unknown day must not permanently hide later missing trading days."""
    calls=[]
    class Session:
        def __init__(self,*a,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def initialize(self):pass
        async def call_tool(self,name,args):
            if name=='archive.status':
                value={'runtime':'GOOGLE_FIRST','local_candle_store':False,'source_sha':'a'*40,'sole_writer':True}
            elif name=='archive.sync':
                calls.append(args)
                if args['from_date']=='2026-01-01':
                    value={'status':'PARTIAL' if window_status=='PARTIAL_WITH_CURSOR' else window_status,'results':[{'status':child_status}], 'written':False}
                    if window_status=='PARTIAL_WITH_CURSOR':value['next_day']='2026-01-02'
                else:value={'status':'PASS','results':[{'status':'READBACK_PASS'}], 'written':True}
            else:raise AssertionError(name)
            return SimpleNamespace(is_error=False,structured_content=value)
    @asynccontextmanager
    async def streams(*a,**kw):yield (None,None)
    monkeypatch.setattr(client,'ClientSession',Session)
    monkeypatch.setattr(client,'streamable_http_client',streams)
    monkeypatch.setenv('BIRZHA_SOURCE_SHA','a'*40)
    result=asyncio.run(client.cycle('http://127.0.0.1:18085/mcp','isolated-test-token',from_date='2026-01-01',till_date='2026-01-05',timeframes=['D1'],dry_run=False))
    assert result['status']=='PARTIAL'  # Missing evidence never becomes full acceptance.
    for symbol in ('RI','Si','BR','GOLD','SBER','IMOEX'):
        windows=[c for c in calls if c['symbol']==symbol]
        assert len(windows)==windows_per_symbol
        if windows_per_symbol==2:
            assert windows[1]['from_date']=='2026-01-04'
            assert windows[1]['till_date']=='2026-01-05'


def test_recent_tail_is_checked_even_during_2025_backfill(monkeypatch):
    calls=[]
    class Session:
        def __init__(self,*a,**k):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def initialize(self):pass
        async def call_tool(self,name,args):
            if name=='archive.status':
                v={'runtime':'GOOGLE_FIRST','local_candle_store':False,'source_sha':'a'*40,'sole_writer':True}
            elif name=='archive.coverage':
                v={'timeframes':{'D1':{'last_date':'2026-10-29','archive_start':'2025-01-01',
                                      'scan_cursor':'2025-01-01'}}}
            elif name=='archive.sync':
                calls.append(args)
                v={'status':'PASS','results':[{'status':'READBACK_PASS'}]}
            else:raise AssertionError(name)
            return SimpleNamespace(is_error=False,structured_content=v)
    @asynccontextmanager
    async def streams(*a,**k):yield (None,None)
    monkeypatch.setattr(client,'ClientSession',Session)
    monkeypatch.setattr(client,'streamable_http_client',streams)
    monkeypatch.setenv('BIRZHA_SOURCE_SHA','a'*40)
    result=asyncio.run(client.cycle('http://127.0.0.1:18085/mcp','test-token',
        from_date=None,till_date='2026-10-30',timeframes=['D1'],dry_run=False,
        scan_days_per_run=3,tail_days=4))
    assert result['status']=='PASS'
    for symbol in ('RI','Si','BR','GOLD','SBER','IMOEX'):
        rows=[r for r in calls if r['symbol']==symbol]
        assert len(rows)==3
        assert [r['scan_mode'] for r in rows]==[False,False,True]
        assert [(r['from_date'],r['till_date']) for r in rows]==[
            ('2026-10-27','2026-10-29'),('2026-10-30','2026-10-30'),
            ('2025-01-01','2025-01-03')]
