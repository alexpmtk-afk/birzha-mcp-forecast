"""Separate Google-first MCP runtime; never imports history/Forecast DuckDB stores."""
import os
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from birzha.application.google_first_archive import GoogleFirstArchive,SYMBOLS,ARCHIVE_EXPIRY_POLICY
from birzha.application.market_data import MarketDataService
from birzha.storage.google_archive_transport import GoogleArchiveTransport
from birzha.storage.google_mirror_auth import GoogleRefreshToken
from birzha.security import McpBearerAuthMiddleware
from birzha.application.canonical_market_mirror import MirrorConflict
from birzha.application.sbprox_month import MonthlyJobs

SCOPES=('https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive')

def build_app(service,token,*,sbprox_month_writer=False):
    if not token:raise ValueError('ARCHIVE_MCP_AUTH_REQUIRED')
    mcp=MCPServer(name='BIRZHA GOOGLE ARCHIVE',version='1.0')
    monthly = MonthlyJobs(service,allow_write=sbprox_month_writer)
    read=ToolAnnotations(read_only_hint=True,destructive_hint=False,idempotent_hint=True,open_world_hint=False)
    @mcp.tool(name='archive.status',annotations=read)
    def status()->dict:
        return {'runtime':'GOOGLE_FIRST','canonical_storage':'GOOGLE_SHEETS','m1_storage':'GOOGLE_DRIVE_ZIP','local_candle_store':False,'local_journal':False,'source_sha':service.source_sha,'sole_writer':service.sole_writer,'sbprox_month_writer':monthly.allow_write,'instruments':list(SYMBOLS),'futures_archive_roll_policy':ARCHIVE_EXPIRY_POLICY}
    @mcp.tool(name='archive.coverage',annotations=read)
    def coverage(symbol:str,timeframes:list[str]|None=None)->dict:
        return service.coverage(symbol=symbol,timeframes=tuple(timeframes if timeframes is not None else ('D1','H1','M15')))
    @mcp.tool(name='archive.collect',description='Google-first one completed Moscow civil day; All futures stay in the nearest series through its official last trading date, then roll the next Moscow date. Preview by default. No local history or source files. Existing rows and formulas preserved.')
    def collect(symbol:str,timeframe:str,day:str,exact_secid:str|None=None,dry_run:bool=True)->dict:
        try:return service.collect(symbol=symbol,timeframe=timeframe,day=day,exact_secid=exact_secid,dry_run=dry_run)
        except Exception:return {'status':'BLOCKED','code':'RECONCILE_CLOUD_STATUS_BEFORE_RETRY','written':'UNKNOWN' if not dry_run else False}
    @mcp.tool(name='archive.sync',description='Bounded Google-first candle sync. D1, H1 and M15 use the same futures expiry policy as archive.collect. RI Model 3 preview by default. Explicit apply can append missing indicator rows after candle readback. Old indicator cells require a separately approved repair plan.')
    def sync(symbol:str,from_date:str,till_date:str,timeframes:list[str]|None=None,
             dry_run:bool=True,max_source_windows:int=6,scan_mode:bool=False,
             model3_mode:str='preview',owner_confirmed:bool=False,
             external_writer_exclusion_confirmed:bool=False)->dict:
        try:return service.sync(symbol=symbol,from_date=from_date,till_date=till_date,
            timeframes=tuple(timeframes if timeframes is not None else ('D1','H1','M15')),
            dry_run=dry_run,max_source_windows=max_source_windows,scan_mode=scan_mode,
            model3_mode=model3_mode,owner_confirmed=owner_confirmed,
            external_writer_exclusion_confirmed=external_writer_exclusion_confirmed)
        except Exception:return {'status':'BLOCKED','code':'RECONCILE_CLOUD_STATUS_BEFORE_RETRY','written':'UNKNOWN' if not dry_run else False}
    @mcp.tool(name='archive.update',description='Same candle and RI Model 3 synchronization as archive.sync.')
    def update(symbol:str,from_date:str,till_date:str,timeframes:list[str]|None=None,
               dry_run:bool=True,max_source_windows:int=6,scan_mode:bool=False,
               model3_mode:str='preview',owner_confirmed:bool=False,
               external_writer_exclusion_confirmed:bool=False)->dict:
        return sync(symbol,from_date,till_date,timeframes,dry_run,max_source_windows,scan_mode,
                    model3_mode,owner_confirmed,external_writer_exclusion_confirmed)
    @mcp.tool(name='archive.model3_coverage',annotations=read,description='Read-only RI D1/H1/M15 source-key versus existing indicator-key coverage. Does not compute indicators, attest PIT, or write anything.')
    def model3_coverage(timeframe:str='D1')->dict:
        try:return service.model3_coverage(timeframe=timeframe)
        except Exception:return {'status':'BLOCKED','code':'MODEL3_COVERAGE_READ_FAILED','written':False}
    @mcp.tool(name='archive.model3_data_readiness',annotations=read,
              description='Read-only 2026 RI Model3 source/feature quality and missing-field counts. No source download, historical PIT attestation, or signals.')
    def model3_data_readiness(timeframe:str='H1')->dict:
        try:return service.model3_data_readiness(timeframe=timeframe)
        except Exception:return {'status':'BLOCKED','code':'MODEL3_READINESS_READ_FAILED','written':False}

    @mcp.tool(name='archive.model3_repair_fields',description='Bounded 2026 RI blank-only field repair. Preview by default; apply requires approved plan hash, explicit sole writer/owner/external exclusion, OS guard, cloud receipts and readback. No source downloads or forecast admission.')
    def model3_repair_fields(timeframe:str,from_date:str,till_date:str,max_rows:int=50,
                            max_cells:int=50,fields:list[str]|None=None,after_key:str='',
                            dry_run:bool=True,owner_confirmed:bool=False,
                            external_writer_exclusion_confirmed:bool=False,
                            expected_plan_sha256:str='')->dict:
        try:return service.model3_repair_fields(timeframe=timeframe,from_date=from_date,
            till_date=till_date,max_rows=max_rows,max_cells=max_cells,fields=fields,
            after_key=after_key,dry_run=dry_run,owner_confirmed=owner_confirmed,
            external_writer_exclusion_confirmed=external_writer_exclusion_confirmed,
            expected_plan_sha256=expected_plan_sha256)
        except Exception:return {'status':'BLOCKED','code':'MODEL3_REPAIR_RECONCILE_BEFORE_RETRY','written':False if dry_run else 'UNKNOWN'}

    @mcp.tool(name='archive.model3_backfill',description='RI-only bounded research indicator preview (default); apply requires explicit ownership, OS workbook guard and QUALITY_AUDIT recovery; appends only, never shifts existing rows. Existing 95- or 107-column D1/H1/M15 indicator sheets only. No signal admission, no automatic retries.')
    def model3_backfill(timeframe:str,from_date:str,till_date:str,
                        max_rows:int=50,dry_run:bool=True,
                        owner_confirmed:bool=False,
                        include_minute_proxies:bool=False)->dict:
        try:return service.model3_backfill(timeframe=timeframe,from_date=from_date,
            till_date=till_date,max_rows=max_rows,dry_run=dry_run,
            owner_confirmed=owner_confirmed,
            include_minute_proxies=include_minute_proxies)
        except Exception:return {'status':'BLOCKED','code':'MODEL3_RECONCILE_OR_PREFLIGHT_FAILED','written':'UNKNOWN' if not dry_run else False}
    @mcp.tool(name='archive.sbprox_contract', annotations=read,
              description='RI SBProX source and field contract. OI and trade count remain unverified. Owner loads local cache; reviewed archive is on Google Drive.')
    def sbprox_contract()->dict:
        from birzha.application.sbprox_integration import source_contract
        return source_contract()

    @mcp.tool(name='archive.sbprox_features', annotations=read,
              description='Read the existing RI SBProX master ZIP from Drive and aggregate exact-contract M15/H1/D1 research fields. Explicit timezone and at most seven days. Partial windows and unknown historical availability are labelled. No sheet writes.')
    def sbprox_features(exact_secid:str, timeframe:str, start:str, end:str)->dict:
        from birzha.application.sbprox_integration import aggregate, indexed_minute_reference
        try:
            source = service.transport.read_sbprox_master()
            return aggregate(source, exact_secid, timeframe, start, end, service.now(),
                             reference_minutes=indexed_minute_reference(service.transport))
        except Exception:
            return {'status':'BLOCKED','code':'SBPROX_SOURCE_OR_WINDOW_VALIDATION_FAILED','written':False}

    @mcp.tool(name='archive.sbprox_sheet_plan', annotations=read,
              description='Review-only blank-field plan for existing RI indicator sheets. Compares source candles, preserves existing values, proposes separately named SBProX columns and reports quality/schema blockers. Does not apply the plan.')
    def sbprox_sheet_plan(exact_secid:str, timeframe:str, start:str, end:str)->dict:
        from birzha.application.sbprox_field_repair import repair
        try:
            return repair(service, exact_secid=exact_secid,timeframe=timeframe,start=start,end=end)
        except Exception:
            return {'status':'BLOCKED','code':'SBPROX_SHEET_PLAN_VALIDATION_FAILED','written':False}

    @mcp.tool(name='archive.sbprox_repair_fields',
              description='Bounded RI SBProX blank-cell plan, dry-run by default. Apply requires exact approved schema/plan hashes, sole writer, owner confirmation and exclusion of external editors. Existing writer guard and cloud receipts; no automatic retry or schema changes.')
    def sbprox_repair_fields(exact_secid:str,timeframe:str,start:str,end:str,max_cells:int=12,
                            dry_run:bool=True,owner_confirmed:bool=False,
                            external_writer_exclusion_confirmed:bool=False,
                            expected_plan_sha256:str='',approved_schema_sha256:str='')->dict:
        from birzha.application.sbprox_field_repair import repair
        try:
            return repair(service,exact_secid=exact_secid,timeframe=timeframe,start=start,end=end,
                          max_cells=max_cells,dry_run=dry_run,owner_confirmed=owner_confirmed,
                          external_writer_exclusion_confirmed=external_writer_exclusion_confirmed,
                          expected_plan_sha256=expected_plan_sha256,approved_schema_sha256=approved_schema_sha256)
        except Exception:
            return {'status':'BLOCKED','code':'SBPROX_RECONCILE_BEFORE_RETRY',
                    'written':False if dry_run else 'UNKNOWN'}

    @mcp.tool(name='archive.sbprox_fill_month',
              description='Start a bounded RI H1 month in the background. Preview by default. Apply the reviewed plan hash with an authorized sole writer. Poll archive.sbprox_month_status.')
    def sbprox_fill_month(month:str,dry_run:bool=True,owner_confirmed:bool=False,
                          external_writer_exclusion_confirmed:bool=False,expected_plan_sha256:str='')->dict:
        try:
            return monthly.start(month,dry_run,owner_confirmed,external_writer_exclusion_confirmed,expected_plan_sha256)
        except MirrorConflict as exc:
            return {'job_state':'BLOCKED','code':str(exc),'written':False}

    @mcp.tool(name='archive.sbprox_sync',
              description='Plan or apply RI H1 SBProX from saved Drive data into existing Google rows. Supply month, year, inclusive from_date/till_date, or no dates to discover the current source range and fill holes. Dry-run by default. Poll archive.sbprox_month_status.')
    def sbprox_sync(month:str='',year:int|None=None,from_date:str='',till_date:str='',
                    dry_run:bool=True,owner_confirmed:bool=False,
                    external_writer_exclusion_confirmed:bool=False,expected_plan_sha256:str='')->dict:
        try:
            return monthly.start_sync(month,year,from_date,till_date,dry_run,owner_confirmed,
                                      external_writer_exclusion_confirmed,expected_plan_sha256)
        except MirrorConflict as exc:
            return {'job_state':'BLOCKED','code':str(exc),'written':False}

    @mcp.tool(name='archive.sbprox_month_status',annotations=read,
              description='Read progress, confirmed batches and exact RI month gaps. This call does not start a write.')
    def sbprox_month_status(job_id:str,offset:int=0,limit:int=50)->dict:
        return monthly.status(job_id,offset,limit)

    @mcp.tool(name='archive.ri_hourly_series',annotations=read,
              description='Start a read-only RI research view with at most one selected contract per H1. Uses the same official expiry rollover as archive.collect on the Moscow calendar date. Reports missing data for the selected contract. Poll archive.sbprox_month_status.')
    def ri_hourly_series(month:str)->dict:
        try:
            return monthly.start_series(month)
        except MirrorConflict as exc:
            return {'job_state':'BLOCKED','code':str(exc),'written':False}

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
    return build_app(service,token,sbprox_month_writer=os.getenv('BIRZHA_ARCHIVE_SBPROX_MONTH_WRITER')=='true')

def main():
    import uvicorn
    # A separate loopback endpoint leaves Forecast/Outcome and the existing HOME untouched.
    uvicorn.run(configured_app(),host='127.0.0.1',port=int(os.getenv('BIRZHA_ARCHIVE_PORT','18085')))

if __name__=='__main__':main()
