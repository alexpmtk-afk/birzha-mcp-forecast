"""Month acceptance through the registered MCP tools and an isolated Google book."""
import asyncio
from copy import deepcopy
import json

import httpx

from birzha.application import sbprox_month
from birzha.application.sbprox_integration import CLOCK_WINDOW, OUTPUTS
from birzha.mcp.archive_server import build_app
from test_sbprox_field_repair import Book, service, HEADER, ARGS


def test_month_mcp_fills_multiple_batches_and_preserves_parallel_contract(monkeypatch):
    book = Book({'sha256':'a'*64})
    index = {name:i for i,name in enumerate(HEADER)}
    # Two parallel contracts at 09:00, then another observed RIH6 hour.
    row = book.rows[2]
    row[index['timeframe']] = 'H1'
    row[index['end']] = '2026-02-11T09:59:59+03:00'
    book.raw[1][-1] = row[index['end']]
    parallel = list(row)
    parallel[index['secid']] = 'RIM6'
    parallel[index['record_key']] = 'parallel'
    parallel[index['source_record_key']] = 'parallel-raw'
    later = list(row)
    later[index['begin']] = '2026-02-11T10:00:00+03:00'
    later[index['end']] = '2026-02-11T10:59:59+03:00'
    later[index['record_key']] = 'later'
    later[index['source_record_key']] = 'later-raw'
    book.rows.extend([parallel,later])
    book.raw.extend([['parallel-raw','RIM6',parallel[index['begin']],100,100,100,100,150,True,'raw-sha',parallel[index['end']]],
                     ['later-raw','RIH6',later[index['begin']],100,100,100,100,150,True,'raw-sha',later[index['end']]]])
    original = deepcopy(book.rows)
    values = {f:1 for f in OUTPUTS}
    values['sbprox_quality'] = CLOCK_WINDOW

    def aggregate(source, secid, tf, left, right, now, **kwargs):
        rows = [dict(secid=secid,timeframe=tf,begin=begin,ohlcv=[100,100,100,100,150],
                     values=values,moex_m1_reference=None)
                for begin in (row[index['begin']],later[index['begin']])
                if secid=='RIH6' and left<=begin<right]
        return {'rows':rows}

    monkeypatch.setattr(sbprox_month,'aggregate',aggregate)
    monkeypatch.setattr(sbprox_month,'ROWS_PER_BATCH',1)
    app = build_app(service(book,writer=False),'test',sbprox_month_writer=True)

    async def check():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                headers={'Authorization':'Bearer test','Accept':'application/json, text/event-stream'}
                seq=0

                async def rpc(method,params):
                    nonlocal seq
                    seq+=1
                    response=await client.post('/mcp',headers=headers,json=dict(jsonrpc='2.0',id=seq,method=method,params=params))
                    return response

                init=await rpc('initialize',{'protocolVersion':'2025-03-26','capabilities':{},
                                             'clientInfo':{'name':'test','version':'1'}})
                if init.headers.get('mcp-session-id'):headers['mcp-session-id']=init.headers['mcp-session-id']
                headers['MCP-Protocol-Version']=init.json()['result']['protocolVersion']
                await client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','method':'notifications/initialized'})

                async def tool(name,args):
                    response=await rpc('tools/call',dict(name=name,arguments=args))
                    result=response.json()['result']
                    return result.get('structuredContent') or json.loads(result['content'][0]['text'])

                async def month(**kwargs):
                    out=await tool('archive.sbprox_fill_month',dict(month='2026-02',**kwargs))
                    assert out['job_id']
                    for _ in range(100):
                        out=await tool('archive.sbprox_month_status',dict(job_id=out['job_id']))
                        if out['job_state']!='RUNNING':
                            assert out['job_state']=='DONE',out
                            return out['result']
                        await asyncio.sleep(.01)
                    raise AssertionError('month job did not complete')

                p=await month()
                assert p['planned_cells']==24 and p['parallel_contract_hours']==1 and p['gap_rows']==1
                assert p['gaps'][0]['secid']=='RIM6' and p['gaps'][0]['other_contracts_observed']==['RIH6']
                assert book.writes==0 and not book.audit
                applied=await month(dry_run=False,owner_confirmed=True,
                    external_writer_exclusion_confirmed=True,expected_plan_sha256=p['plan_sha256'])
                assert applied['confirmed_cells']==24 and book.writes==2
                assert book.rows[3]==original[3] and all(r[:95]==old[:95] for r,old in zip(book.rows,original))
                assert [e['status'] for e in book.audit]==['STARTED','PREPARED','COMMITTED']*2
                assert (await month())['planned_cells']==0 and book.writes==2
    asyncio.run(check())


def test_hourly_view_keeps_causal_selection_even_when_other_contract_has_sbprox():
    from types import SimpleNamespace
    book = Book({'sha256':'a'*64})
    idx = {f:i for i,f in enumerate(HEADER)}
    book.rows[2][idx['timeframe']] = 'H1'
    book.rows[2][idx['sbprox_volume_contracts']] = 150
    parallel = list(book.rows[2])
    parallel[idx['secid']] = 'RIM6'
    parallel[idx['source_record_key']] = 'parallel-raw'
    parallel[idx['sbprox_volume_contracts']] = ''
    book.rows.append(parallel)
    book.raw[0].append('trade_session_date')
    book.raw[1].append('2026-02-12')
    r = list(book.raw[1]);r[0]='parallel-raw';r[1]='RIM6';book.raw.append(r)
    s = service(book)
    calls = []
    def resolve(symbol,day):
        calls.append((symbol,day.isoformat()))
        return SimpleNamespace(secid='RIM6',source='MOEX_ISS_HISTORY_T_MINUS_1_RECONSTRUCTED')
    s.market = SimpleNamespace(historical_future_resolver=SimpleNamespace(resolve=resolve))
    result = sbprox_month.hourly_series(s,'2026-02')
    assert calls==[('RI','2026-02-12')]
    assert result['distinct_hours']==1 and result['forecast_admission']=='NOT_GRANTED'
    row = result['rows'][0]
    assert row['selected_contract']=='RIM6' and row['sheet_row']==4 and row['status']=='SBPROX_MISSING'
    assert row['values']=={} and not book.writes

def test_month_lost_reply_keeps_pending_receipt_and_does_not_resend():
    import pytest
    from birzha.application.canonical_market_mirror import MirrorConflict, digest
    from birzha.application.model3_write_audit import recover
    book = Book({'sha256':'a'*64})
    s = service(book)
    old = deepcopy(book.rows)
    original = book.model3_write_cells
    def lose_response(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('reply lost')
    book.model3_write_cells = lose_response
    report = dict(month='2026-02',plan_sha256='b'*64,source_sha256='a'*64)
    selected = [{'moex_m1_reference':None,'cells':[dict(row_index=2,column_index=95,value=150)]}]
    with pytest.raises(MirrorConflict,match='UNKNOWN_OUTCOME'):
        sbprox_month.apply_batches(s,report,selected,old,digest(book.raw),lambda **kw:None)
    assert [r['status'] for r in book.audit]==['STARTED','PREPARED']
    result = recover(s)
    assert result['status']=='RECOVERED_COMMITTED' and result['confirmed_cells']==1
    assert book.writes==1 and book.rows[2][95]==150
