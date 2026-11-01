"""Separate Google-first MCP runtime; never imports history/Forecast DuckDB stores."""
import os
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from birzha.application.google_first_archive import GoogleFirstArchive,SYMBOLS
from birzha.application.market_data import MarketDataService
from birzha.storage.google_archive_transport import GoogleArchiveTransport
from birzha.storage.google_mirror_auth import GoogleRefreshToken
from birzha.security import McpBearerAuthMiddleware
from birzha.application.canonical_market_mirror import MirrorConflict

SCOPES=('https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive')

def build_app(service,token):
    if not token:raise ValueError('ARCHIVE_MCP_AUTH_REQUIRED')
    mcp=MCPServer(name='BIRZHA GOOGLE ARCHIVE',version='1.0')
    read=ToolAnnotations(read_only_hint=True,destructive_hint=False,idempotent_hint=True,open_world_hint=False)
    @mcp.tool(name='archive.status',annotations=read)
    def status()->dict:
        return {'runtime':'GOOGLE_FIRST','canonical_storage':'GOOGLE_SHEETS','m1_storage':'GOOGLE_DRIVE_ZIP','local_candle_store':False,'local_journal':False,'source_sha':service.source_sha,'sole_writer':service.sole_writer,'instruments':list(SYMBOLS)}
    @mcp.tool(name='archive.coverage',annotations=read)
    def coverage(symbol:str,timeframes:list[str]|None=None)->dict:
        return service.coverage(symbol=symbol,timeframes=tuple(timeframes if timeframes is not None else ('D1','H1','M15')))
    @mcp.tool(name='archive.collect',description='Google-first one completed civil day, exact historical T-1 contract; preview by default. No local history or source files. Existing rows and formulas preserved.')
    def collect(symbol:str,timeframe:str,day:str,exact_secid:str|None=None,dry_run:bool=True)->dict:
        try:return service.collect(symbol=symbol,timeframe=timeframe,day=day,exact_secid=exact_secid,dry_run=dry_run)
        except Exception:return {'status':'BLOCKED','code':'RECONCILE_CLOUD_STATUS_BEFORE_RETRY','written':'UNKNOWN' if not dry_run else False}
    @mcp.tool(name='archive.sync',description='Bounded Google-first D1/H1/M15 collection; at most three days, explicit budget. Does not calculate continuous research coordinates or modify existing indicator formulas.')
    def sync(symbol:str,from_date:str,till_date:str,timeframes:list[str]|None=None,dry_run:bool=True,max_source_windows:int=6,scan_mode:bool=False)->dict:
        try:return service.sync(symbol=symbol,from_date=from_date,till_date=till_date,timeframes=tuple(timeframes if timeframes is not None else ('D1','H1','M15')),dry_run=dry_run,max_source_windows=max_source_windows,scan_mode=scan_mode)
        except Exception:return {'status':'BLOCKED','code':'RECONCILE_CLOUD_STATUS_BEFORE_RETRY','written':'UNKNOWN' if not dry_run else False}
    @mcp.tool(name='archive.update',description='Same bounded Google-first synchronization; no DuckDB mirror or local backup prerequisite.')
    def update(symbol:str,from_date:str,till_date:str,timeframes:list[str]|None=None,dry_run:bool=True,max_source_windows:int=6,scan_mode:bool=False)->dict:
        return sync(symbol,from_date,till_date,timeframes,dry_run,max_source_windows,scan_mode)
    @mcp.tool(name='archive.model3_coverage',annotations=read,description='Read-only RI D1/H1/M15 source-key versus existing indicator-key coverage. Does not compute indicators, attest PIT, or write anything.')
    def model3_coverage(timeframe:str='D1')->dict:
        try:return service.model3_coverage(timeframe=timeframe)
        except Exception:return {'status':'BLOCKED','code':'MODEL3_COVERAGE_READ_FAILED','written':False}
    @mcp.custom_route('/healthz',methods=['GET'])
    async def health(request):return JSONResponse({'status':'ok','runtime':'GOOGLE_FIRST','source_sha':service.source_sha})
    app=mcp.streamable_http_app(json_response=True,transport_security=TransportSecuritySettings(allowed_hosts=['127.0.0.1','127.0.0.1:*','localhost','localhost:*'],allowed_origins=[]))
    app.add_middleware(McpBearerAuthMiddleware,token=token)
    return app

def configured_app():
    credential=os.getenv('BIRZHA_ARCHIVE_GOOGLE_CREDENTIALS_PATH')
    token=os.getenv('BIRZHA_MCP_BEARER_TOKEN')
    sha=os.getenv('BIRZHA_SOURCE_SHA','')
    if not credential or not token or len(sha)!=40 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('EXPLICIT_ARCHIVE_CREDENTIALS_AUTH_SHA_REQUIRED')
    if os.getenv('BIRZHA_NATIVE_MIRROR_ENABLED','false').lower()=='true':raise ValueError('LEGACY_AND_GOOGLE_FIRST_WRITERS_CONFLICT')
    transport=GoogleArchiveTransport(GoogleRefreshToken(credential,scopes=SCOPES))
    service=GoogleFirstArchive(transport,MarketDataService.default(),sha,sole_writer=os.getenv('BIRZHA_ARCHIVE_SOLE_WRITER','false').lower()=='true')
    return build_app(service,token)

def main():
    import uvicorn
    # A separate loopback endpoint leaves Forecast/Outcome and the existing HOME untouched.
    uvicorn.run(configured_app(),host='127.0.0.1',port=int(os.getenv('BIRZHA_ARCHIVE_PORT','18085')))

if __name__=='__main__':main()
