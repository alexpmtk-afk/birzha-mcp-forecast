"""In-memory MCP client for the dedicated Google-first archive runtime."""
import argparse,asyncio,json,os
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ARCHIVE_START=date(2025,1,1)

async def cycle(url,token,*,from_date,till_date,timeframes,dry_run,scan_days_per_run=9):
    """A bounded sweep of the whole 2025+ horizon, resumed from Google SYNC_STATUS.

    An explicit from_date is a manual bounded backfill; it never advances the
    independent automatic scan cursor. No temporary disk state is created.
    """
    p=urlsplit(url)
    if p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost') or p.username or p.password or p.path!='/mcp':raise ValueError('EXPLICIT_LOOPBACK_ARCHIVE_ENDPOINT_REQUIRED')
    if not token:raise ValueError('ARCHIVE_AUTH_REQUIRED')
    if type(scan_days_per_run) is not int or not 1<=scan_days_per_run<=31:raise ValueError('INVALID_SCAN_BUDGET')
    if not timeframes or len(set(timeframes))!=len(timeframes) or any(tf not in ('D1','H1','M15') for tf in timeframes):raise ValueError('INVALID_TIMEFRAMES')
    managed=from_date is None
    end=date.fromisoformat(till_date)
    yesterday=datetime.now(ZoneInfo('Europe/Moscow')).date()-timedelta(days=1)
    if managed:end=min(end,yesterday)
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=1800) as client:
        async with streamable_http_client(url,http_client=client) as streams:
            async with ClientSession(*streams,read_timeout_seconds=1800) as session:
                await session.initialize()
                status=await session.call_tool('archive.status',{})
                if status.is_error:raise RuntimeError('ARCHIVE_STATUS_FAILED')
                data=status.structured_content or json.loads(status.content[0].text)
                if data.get('runtime')!='GOOGLE_FIRST' or data.get('local_candle_store') is not False:raise RuntimeError('WRONG_ARCHIVE_RUNTIME')
                if not dry_run and data.get('sole_writer') is not True:raise RuntimeError('ARCHIVE_SOLE_WRITER_REQUIRED')
                expected=os.getenv('BIRZHA_SOURCE_SHA')
                if not expected or data.get('source_sha')!=expected:raise RuntimeError('ARCHIVE_SHA_MISMATCH')
                results=[]
                for symbol in ('RI','Si','BR','GOLD','SBER','IMOEX'):
                    if managed:
                        r=await session.call_tool('archive.coverage',{'symbol':symbol,'timeframes':timeframes})
                        if r.is_error:
                            results.append({'symbol':symbol,'status':'CLOUD_COVERAGE_FAILED'});continue
                        v=r.structured_content or json.loads(r.content[0].text)
                        snapshots=v.get('timeframes',{})
                    else:snapshots={}
                    for tf in timeframes:
                        if managed:
                            snapshot=snapshots.get(tf,{})
                            cursor=snapshot.get('scan_cursor')
                            if snapshot.get('archive_start')!=ARCHIVE_START.isoformat() or not isinstance(cursor,str):
                                results.append({'symbol':symbol,'timeframe':tf,'status':'CLOUD_SCAN_CURSOR_REQUIRED'});continue
                            try:left=date.fromisoformat(cursor)
                            except ValueError:
                                results.append({'symbol':symbol,'timeframe':tf,'status':'INVALID_CLOUD_SCAN_CURSOR'});continue
                            if left<ARCHIVE_START or left>yesterday:
                                results.append({'symbol':symbol,'timeframe':tf,'status':'INVALID_CLOUD_SCAN_CURSOR'});continue
                            window_end=min(end,left+timedelta(days=scan_days_per_run-1))
                        else:
                            left=date.fromisoformat(from_date);window_end=end
                            if (end-left).days>31:
                                results.append({'symbol':symbol,'timeframe':tf,'status':'BOUNDED_BACKFILL_REQUIRED'});continue
                        if left>window_end:
                            results.append({'symbol':symbol,'timeframe':tf,'status':'UP_TO_DATE'});continue
                        while left<=window_end:
                            right=min(left+timedelta(days=2),window_end)
                            r=await session.call_tool('archive.sync',{
                                'symbol':symbol,'from_date':left.isoformat(),'till_date':right.isoformat(),
                                'timeframes':[tf],'dry_run':dry_run,'max_source_windows':12,
                                'scan_mode':managed})
                            v=r.structured_content or json.loads(r.content[0].text)
                            status='ERROR' if r.is_error else v.get('status','ERROR')
                            results.append({'symbol':symbol,'timeframe':tf,'status':status})
                            # In managed mode archive.sync updates the cloud cursor
                            # only after every explicitly safe day. Unknown results,
                            # conflicts or unconsumed source budget stop this scope.
                            safe_partial=(status=='PARTIAL' and not v.get('next_day')
                                and isinstance(v.get('results'),list) and bool(v['results'])
                                and all(isinstance(item,dict) and item.get('status') in (
                                    'READBACK_PASS','REUSED','SOURCE_NOT_AVAILABLE',
                                    'SOURCE_NOT_AVAILABLE_RETRY_LATER','EXISTING_UNVERIFIED_NO_FETCH'
                                ) for item in v['results']))
                            if status not in ('PASS','PLANNED','UP_TO_DATE') and not safe_partial:break
                            left=right+timedelta(days=1)
                return {'status':'PASS' if all(r['status'] in ('PASS','PLANNED','UP_TO_DATE') for r in results) else 'PARTIAL','results':results}

def main():
    p=argparse.ArgumentParser();p.add_argument('--from-date');p.add_argument('--till-date');p.add_argument('--timeframes',default='D1');p.add_argument('--apply',action='store_true');p.add_argument('--scan-days-per-run',type=int,default=9);a=p.parse_args()
    till=a.till_date or (datetime.now(ZoneInfo('Europe/Moscow')).date()-timedelta(days=1)).isoformat()
    result=asyncio.run(cycle(os.getenv('BIRZHA_ARCHIVE_MCP_URL','http://127.0.0.1:18085/mcp'),os.getenv('BIRZHA_MCP_BEARER_TOKEN',''),from_date=a.from_date,till_date=till,timeframes=a.timeframes.split(','),dry_run=not a.apply,scan_days_per_run=a.scan_days_per_run))
    print(json.dumps(result));return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
