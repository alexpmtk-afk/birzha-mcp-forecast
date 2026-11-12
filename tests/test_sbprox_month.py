"""Month acceptance through the registered MCP tools and an isolated Google book."""
import asyncio
from copy import deepcopy
from datetime import datetime

import pytest
import json

import httpx

from birzha.application import sbprox_month
from birzha.application.sbprox_integration import CLOCK_WINDOW, OUTPUTS
from birzha.mcp.archive_server import build_app
from test_sbprox_field_repair import Book, service, HEADER, ARGS
from test_sbprox_integration import reference, source


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
    latest = list(later)
    latest[index['begin']] = '2026-02-11T11:00:00+03:00'
    latest[index['end']] = '2026-02-11T11:59:59+03:00'
    latest[index['record_key']] = 'latest'
    latest[index['source_record_key']] = 'latest-raw'
    book.rows.extend([parallel, later, latest])
    book.raw.extend([['parallel-raw','RIM6',parallel[index['begin']],100,100,100,100,150,True,'raw-sha',parallel[index['end']]],
                     ['later-raw','RIH6',later[index['begin']],100,100,100,100,150,True,'raw-sha',later[index['end']]]])
    book.raw.append(['latest-raw','RIH6',latest[index['begin']],100,100,100,100,150,True,'raw-sha',latest[index['end']]])
    original = deepcopy(book.rows)
    values = {f:1 for f in OUTPUTS}
    values['sbprox_quality'] = CLOCK_WINDOW

    def observed(service, source, windows, reference):
        return [dict(secid=secid,timeframe='H1',begin=begin.isoformat(),
                     ohlcv=[100,100,100,100,150],values=values,moex_m1_reference=None)
                for secid,begin in windows if secid=='RIH6']

    monkeypatch.setattr(sbprox_month, 'observed_hours', observed)
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

                async def job(name='archive.sbprox_fill_month', **kwargs):
                    out=await tool(name, kwargs)
                    assert out['job_id']
                    for _ in range(100):
                        out=await tool('archive.sbprox_month_status',dict(job_id=out['job_id']))
                        if out['job_state']!='RUNNING':
                            assert out['job_state']=='DONE',out
                            return out['result']
                        await asyncio.sleep(.01)
                    raise AssertionError('month job did not complete')

                p=await job(month='2026-02')
                explicit = await job('archive.sbprox_sync', from_date='2026-02-11', till_date='2026-02-11')
                annual = await job('archive.sbprox_sync', year=2026)
                auto = await job('archive.sbprox_sync')
                assert explicit['planned_cells'] == annual['planned_cells'] == auto['planned_cells'] == 36
                assert auto['source_last_hour'] == latest[index['begin']]
                assert auto['first_pending_hour'] == row[index['begin']]
                assert p['planned_cells']==36 and p['parallel_contract_hours']==1 and p['gap_rows']==1
                assert p['gaps'][0]['secid']=='RIM6' and p['gaps'][0]['other_contracts_observed']==['RIH6']
                assert book.writes==0 and not book.audit
                # Simulate a filled newer row from an older archive revision.
                for name in OUTPUTS:
                    book.rows[-1][index[name]] = values[name]
                book.rows[-1][index['sbprox_source_sha256']] = 'previous-revision'
                auto = await job('archive.sbprox_sync')
                assert auto['last_filled_hour'] == latest[index['begin']]
                assert auto['first_pending_hour'] == row[index['begin']] and auto['planned_cells'] == 24
                applied=await job('archive.sbprox_sync', dry_run=False,owner_confirmed=True,
                    external_writer_exclusion_confirmed=True,expected_plan_sha256=auto['plan_sha256'])
                assert applied['confirmed_cells']==24 and book.writes==2
                assert book.rows[3]==original[3] and all(r[:95]==old[:95] for r,old in zip(book.rows,original))
                assert book.rows[-1][index['sbprox_source_sha256']] == 'previous-revision'
                assert [e['status'] for e in book.audit]==['STARTED','PREPARED','COMMITTED']*2
                assert (await job('archive.sbprox_sync'))['planned_cells']==0 and book.writes==2
    asyncio.run(check())


def test_hourly_view_shares_archive_choice_across_moscow_expiry():
    from types import SimpleNamespace
    from birzha.application.google_first_archive import GoogleFirstArchive
    from birzha.domain.market import Instrument
    from birzha.providers.moex_history import ARCHIVE_EXPIRY_POLICY

    book = Book({'sha256':'a'*64})
    idx = {f:i for i,f in enumerate(HEADER)}
    template = book.rows[2]
    book.rows = book.rows[:2]
    book.raw = [book.raw[0] + ['trade_session_date']]
    for secid, day, hour, trade_date, volume in (
        ('RIH6', '2026-03-19', '19', '2026-03-20', 100),
        ('RIM6', '2026-03-19', '19', '2026-03-20', 900),
        ('RIM6', '2026-03-20', '09', '', 700),
        ('RIH6', '2026-03-20', '10', '2026-03-20', 500),
    ):
        begin, end = f'{day}T{hour}:00:00+03:00', f'{day}T{hour}:59:59+03:00'
        key = f'RI|H1|{secid}|{begin}'
        row = list(template)
        for field, value in dict(begin=begin, end=end, timeframe='H1', secid=secid,
                                 source_record_key=key, sbprox_volume_contracts=volume).items():
            row[idx[field]] = value
        book.rows.append(row)
        book.raw.append([key,secid,begin,100,100,100,100,volume,True,'raw-sha',end,trade_date])

    def contract(secid, expiry):
        return Instrument(symbol='RI', secid=secid, board='RFUD', engine='futures',
                          market='forts', asset_class='future', last_trade_date=expiry,
                          roll_policy=ARCHIVE_EXPIRY_POLICY)

    s = GoogleFirstArchive(book, SimpleNamespace(), 'a'*40)
    s._schemas = lambda *args: deepcopy(book.raw)
    s._status = lambda symbol: {
        'google_first_contract|2026-03-19': json.dumps(contract('RIM6','2026-06-18').to_dict()),
        f'google_first_contract|2026-03-20|{ARCHIVE_EXPIRY_POLICY}':
            json.dumps(contract('RIM6','2026-06-18').to_dict()),
    }
    calls = []

    def resolve(symbol, *, as_of):
        calls.append((symbol, as_of.isoformat()))
        return contract('RIH6', '2026-03-19')

    s.market.resolve_archive_contract = resolve
    result = sbprox_month.hourly_series(s, '2026-03')
    assert calls == [('RI', '2026-03-19')]
    assert result['futures_archive_roll_policy'] == ARCHIVE_EXPIRY_POLICY
    assert result['forecast_admission'] == 'NOT_GRANTED'
    expiry, following, missing = result['rows']
    assert (expiry['selected_contract'], expiry['trade_session_date']) == ('RIH6', '2026-03-20')
    assert expiry['values']['sbprox_volume_contracts'] == 100
    assert following['selected_contract'] == 'RIM6'
    assert following['values']['sbprox_volume_contracts'] == 700
    assert missing['status'] == 'SELECTED_CONTRACT_MISSING'
    assert missing['selected_contract'] == 'RIM6' and 'values' not in missing
    assert not book.writes


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


def test_range_bounds_accept_current_month_and_exclude_open_hour():
    now = datetime.fromisoformat('2026-11-11T12:43:00+03:00')
    p, a, b, automatic = sbprox_month.sync_window(now, '2026-11', None, '', '')
    assert p == '2026-11' and a.day == 1 and b == now.replace(minute=0) and not automatic
    _, _, b, automatic = sbprox_month.sync_window(now, '', None, '', '')
    assert b == now.replace(minute=0) and automatic
    _, a, b, _ = sbprox_month.sync_window(now, '', None, '2026-02-28', '2026-03-01')
    assert a.isoformat() == '2026-02-28T00:00:00+03:00'
    assert b.isoformat() == '2026-03-02T00:00:00+03:00'
    with pytest.raises(sbprox_month.MirrorConflict, match='ARGUMENTS_CONFLICT'):
        sbprox_month.sync_window(now, '2026-11', 2026, '', '')
    with pytest.raises(sbprox_month.MirrorConflict, match='NO_COMPLETED_HOUR'):
        sbprox_month.sync_window(now, '2026-12', None, '', '')


def test_source_beyond_canonical_window_is_not_read(source):
    book = Book(source)
    book.read_sbprox_master = lambda: pytest.fail('no canonical target to read')
    s = service(book)
    s.now = lambda: datetime.fromisoformat('2026-11-11T12:43:00+03:00')
    book.rows[2][HEADER.index('begin')] = '2026-02-12T09:00:00+03:00'
    book.rows[2][HEADER.index('timeframe')] = 'H1'
    p = sbprox_month.make_plan(s, 'update',
        datetime.fromisoformat('2026-01-01T00:00:00+03:00'), s.now(), automatic=True)[0]
    assert p['source_last_hour'] is None and p['canonical_last_end'] is None
    assert p['source_without_target_rows'] == 0
    assert p['gap_counts'] == {'CANONICAL_WINDOW_UNVERIFIED': 1}
    assert p['planned_cells'] == 0 and book.writes == 0
