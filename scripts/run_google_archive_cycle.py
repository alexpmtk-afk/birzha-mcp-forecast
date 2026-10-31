"""In-memory MCP client for the dedicated Google-first archive runtime."""
import argparse,asyncio,json,os
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def cycle(url,token,*,from_date,till_date,timeframes,dry_run):
    p=urlsplit(url)
    if p.scheme!='http' or p.hostname not in ('127.0.0.1','localhost') or p.username or p.password or p.path!='/mcp':raise ValueError('EXPLICIT_LOOPBACK_ARCHIVE_ENDPOINT_REQUIRED')
    if not token:raise ValueError('ARCHIVE_AUTH_REQUIRED')
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
                    if from_date is None:
                        r=await session.call_tool('archive.coverage',{'symbol':symbol,'timeframes':timeframes})
                        v=r.structured_content or json.loads(r.content[0].text)
                        dates=[v.get('timeframes',{}).get(tf,{}).get('last_date') for tf in timeframes]
                        if r.is_error or not all(dates):
                            results.append({'symbol':symbol,'status':'EXPLICIT_START_REQUIRED'});continue
                        left=date.fromisoformat(min(dates))+(timedelta(days=1) if timeframes==['D1'] else timedelta())
                    else:left=date.fromisoformat(from_date)
                    end=date.fromisoformat(till_date)
                    if left>end:
                        results.append({'symbol':symbol,'status':'UP_TO_DATE'});continue
                    if (end-left).days>31:
                        results.append({'symbol':symbol,'status':'BOUNDED_BACKFILL_REQUIRED'});continue
                    while left<=end:
                        right=min(left+timedelta(days=2),end)
                        r=await session.call_tool('archive.sync',{'symbol':symbol,'from_date':left.isoformat(),'till_date':right.isoformat(),'timeframes':timeframes,'dry_run':dry_run,'max_source_windows':12})
                        v=r.structured_content or json.loads(r.content[0].text)
                        status='ERROR' if r.is_error else v.get('status','ERROR')
                        results.append({'symbol':symbol,'status':status})
                        # No observations / unverified old rows do not block later days.
                        # Keep the overall PARTIAL result; never continue after unknown
                        # write outcomes, unrecognised failures or an unfinished budget.
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
    p=argparse.ArgumentParser();p.add_argument('--from-date');p.add_argument('--till-date');p.add_argument('--timeframes',default='D1');p.add_argument('--apply',action='store_true');a=p.parse_args()
    till=a.till_date or (datetime.now(ZoneInfo('Europe/Moscow')).date()-timedelta(days=1)).isoformat()
    result=asyncio.run(cycle(os.getenv('BIRZHA_ARCHIVE_MCP_URL','http://127.0.0.1:18085/mcp'),os.getenv('BIRZHA_MCP_BEARER_TOKEN',''),from_date=a.from_date,till_date=till,timeframes=a.timeframes.split(','),dry_run=not a.apply))
    print(json.dumps(result));return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
