import asyncio,json,subprocess,sys,os
from pathlib import Path
import httpx
from test_google_first_archive import setup
from birzha.mcp.archive_server import build_app

def test_actual_mcp_google_first_chain_and_auth():
    b,m,s=setup();app=build_app(s,'isolated-test-token')
    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
                body={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'isolated','version':'1'}}}
                assert (await c.post('/mcp',json=body)).status_code==401
                headers={'Authorization':'Bearer isolated-test-token','Accept':'application/json, text/event-stream'}
                r=await c.post('/mcp',headers=headers,json=body);assert r.status_code==200
                if r.headers.get('mcp-session-id'):headers['mcp-session-id']=r.headers['mcp-session-id']
                headers['MCP-Protocol-Version']=r.json()['result']['protocolVersion']
                await c.post('/mcp',headers=headers,json={'jsonrpc':'2.0','method':'notifications/initialized'})
                async def call(name,args):
                    r=await c.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':name,'arguments':args}})
                    result=r.json()['result'];assert not result.get('isError');return result.get('structuredContent') or json.loads(result['content'][0]['text'])
                status=await call('archive.status',{});assert status['local_candle_store'] is False
                args={'symbol':'RI','timeframe':'D1','day':'2026-10-29','dry_run':False}
                assert (await call('archive.collect',args))['status']=='READBACK_PASS'
                assert (await call('archive.collect',args))['status']=='REUSED'
                b.status['google_first_scan_cursor|D1']='2026-10-29'
                check=await call('archive.sync',{'symbol':'RI','from_date':'2026-10-29',
                    'till_date':'2026-10-29','timeframes':['D1'],'dry_run':False,
                    'max_source_windows':3,'scan_mode':True})
                assert check['status']=='PASS'
                assert check['results'][0]['status']=='REUSED'
                assert b.status['google_first_scan_cursor|D1']=='2026-10-30'
                r=await c.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'history.sync','arguments':{}}})
                assert r.json()['result'].get('isError') is True
    asyncio.run(run());assert len(m.calls)==1

def test_archive_runtime_import_does_not_import_any_persistent_backend():
    code='''import builtins
old=builtins.__import__
def guard(name,*a,**k):
 if name.split('.')[0] in ('duckdb','ydb'):raise AssertionError('persistent backend import: '+name)
 return old(name,*a,**k)
builtins.__import__=guard
import birzha.mcp.archive_server
print('NO_PERSISTENT_BACKEND')
'''
    env={k:v for k,v in os.environ.items() if not k.startswith('BIRZHA_')};env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src');env['PYTHONDONTWRITEBYTECODE']='1'
    r=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,env=env,timeout=30)
    assert r.returncode==0,r.stderr
    assert 'NO_PERSISTENT_BACKEND' in r.stdout
