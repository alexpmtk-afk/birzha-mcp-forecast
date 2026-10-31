import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from scripts import run_google_archive_cycle as client

def test_scheduled_d1_uses_google_last_date_and_only_archive_for_six(monkeypatch):
    calls=[]
    class Session:
        def __init__(self,*a,**kw):assert kw['read_timeout_seconds']==1800
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def initialize(self):pass
        async def call_tool(self,name,args):
            calls.append((name,args))
            if name=='archive.status':v={'runtime':'GOOGLE_FIRST','local_candle_store':False,'source_sha':'a'*40,'sole_writer':True}
            elif name=='archive.coverage':v={'timeframes':{'D1':{'last_date':'2026-10-29'}}}
            elif name=='archive.sync':v={'status':'PASS'}
            else:raise AssertionError('unexpected tool')
            return SimpleNamespace(is_error=False,structured_content=v)
    @asynccontextmanager
    async def streams(*a,**k):yield (None,None)
    monkeypatch.setattr(client,'ClientSession',Session);monkeypatch.setattr(client,'streamable_http_client',streams);monkeypatch.setenv('BIRZHA_SOURCE_SHA','a'*40)
    r=asyncio.run(client.cycle('http://127.0.0.1:18085/mcp','isolated-test-token',from_date=None,till_date='2026-10-30',timeframes=['D1'],dry_run=False))
    writes=[a for n,a in calls if n=='archive.sync']
    assert r['status']=='PASS' and len(writes)==6
    assert [a['symbol'] for a in writes]==['RI','Si','BR','GOLD','SBER','IMOEX']
    assert all(a['from_date']==a['till_date']=='2026-10-30' and a['timeframes']==['D1'] for a in writes)

@pytest.mark.parametrize('url',['https://example.org/mcp','http://127.0.0.1:18085/wrong','http://user:pass@127.0.0.1:18085/mcp'])
def test_scheduler_rejects_unapproved_endpoint(url):
    with pytest.raises(ValueError,match='LOOPBACK'):asyncio.run(client.cycle(url,'isolated-test-token',from_date=None,till_date='2026-10-30',timeframes=['D1'],dry_run=True))
