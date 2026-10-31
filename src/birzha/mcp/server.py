"""BIRZHA MCP server surface.

MCP remains a thin interface: market, historical data, flow, snapshot, forecast,
outcome, validation and persistence logic lives in application/storage layers.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from birzha.application.calibration import ModelCalibrationService, calibrate_across_symbols
from birzha.application.flow import MarketFlowService
from birzha.application.forecast import ForecastService
from birzha.application.forecast_input_readiness import ForecastInputReadinessService
from birzha.application.historical_flow import HistoricalFlowDataService
from birzha.application.historical_data import HistoricalDataService
from birzha.application.journal import ForecastJournalService
from birzha.application.market_data import MarketDataService
from birzha.application.market_analysis import MarketAnalysisService
from birzha.application.model_lab import ModelAcceptanceService
from birzha.application.method_validation import MethodWalkForwardValidator
from birzha.application.outcome import OutcomeService
from birzha.application.outcome_contract import OutcomeContractService
from birzha.application.prediction import PredictionContractService
from birzha.application.snapshot import MarketSnapshotService
from birzha.application.upstream_control import ProcessUpstreamControlPlane
from birzha.application.validation import WalkForwardValidator
from birzha.config import Settings
from birzha.providers.moex_analytics import MoexAnalyticsClient
from birzha.providers.moex_calendar import MoexTradingCalendar
from birzha.security import McpBearerAuthMiddleware
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.historical_store import DuckDBHistoricalCandleStore
from birzha.storage.historical_flow_store import DuckDBHistoricalFlowStore
from birzha.storage.outcome_journal import DuckDBOutcomeJournal
from birzha.storage.ydb_historical_store import YdbHistoricalCandleStore
from birzha.storage.ydb_historical_flow_store import YdbHistoricalFlowStore
from birzha.storage.ydb_rate_gate import YdbSlotPacingGate
from birzha.storage.ydb_state import YdbForecastJournal, YdbOutcomeJournal, YdbRuntime
from birzha.version import ARCHITECTURE_VERSION, SERVICE_NAME, VERSION

CORE_HISTORY_SYMBOLS = ["SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"]
CORE_HISTORY_TIMEFRAMES = ["D1", "H1", "M15"]

settings = Settings.from_env()
mcp = MCPServer(name=SERVICE_NAME, version=VERSION)

_ydb_runtime: YdbRuntime | None = None
if settings.state_backend == "ydb":
    assert settings.ydb_connection_string is not None
    _ydb_runtime = YdbRuntime.connect(settings.ydb_connection_string)
    _upstream_control = ProcessUpstreamControlPlane(
        gate_factory=lambda provider_key: YdbSlotPacingGate(
            _ydb_runtime.pool,
            provider_key=provider_key,
        ),
        require_distributed_gate=True,
    )
    _journal_store = YdbForecastJournal(_ydb_runtime.pool)
    _outcome_store = YdbOutcomeJournal(_ydb_runtime.pool)
    _historical_store = YdbHistoricalCandleStore(_ydb_runtime.pool)
    _historical_flow_store = YdbHistoricalFlowStore(_ydb_runtime.pool)
else:
    _upstream_control = ProcessUpstreamControlPlane()
    _journal_store = DuckDBForecastJournal(settings.forecast_journal_path)
    _outcome_store = DuckDBOutcomeJournal(settings.forecast_journal_path)
    _historical_store = DuckDBHistoricalCandleStore(settings.historical_store_path)
    _historical_flow_store = DuckDBHistoricalFlowStore(settings.historical_store_path)

_minute_capture = None
if settings.model3_minute_archive_dir:
    from birzha.storage.model3_minute_archive import MinuteArchiveCapture
    _minute_capture = MinuteArchiveCapture(settings.model3_minute_archive_dir)
_market = MarketDataService.default(control_plane=_upstream_control, minute_observer=_minute_capture)
_history = HistoricalDataService(market_data=_market, store=_historical_store)
_analytics = MoexAnalyticsClient(control_plane=_upstream_control)
_historical_flow = HistoricalFlowDataService(market_data=_market, analytics=_analytics, store=_historical_flow_store)
_flow = MarketFlowService(market_data=_market, analytics=_analytics, historical=_historical_flow)
_snapshot = MarketSnapshotService(market_data=_market, flow=_flow)
_forecast = ForecastService(snapshots=_snapshot)
_prediction_contract = PredictionContractService(snapshots=_snapshot)
_outcome_contract = OutcomeContractService(
    market_data=_market,
    prediction_contracts=_prediction_contract,
    analytics=_analytics,
)
_forecast_input_readiness = ForecastInputReadinessService(market_data=_market)
_journal = ForecastJournalService(forecasts=_forecast, journal=_journal_store)  # type: ignore[arg-type]
_analysis = MarketAnalysisService(history=_history, journal=_journal)
_outcomes = OutcomeService(market_data=_market, forecasts=_journal_store, outcomes=_outcome_store)  # type: ignore[arg-type]
_validator = WalkForwardValidator(
    market_data=_market,
    forecasts=_forecast,
    calendar=MoexTradingCalendar(_market.provider),
    history=_history,
    historical_flow=_historical_flow,
)
_method_validator = MethodWalkForwardValidator(base=_validator)
_model_lab = ModelAcceptanceService(validator=_validator)
_calibration = ModelCalibrationService(validator=_validator)


_native_mirror = None
if settings.native_mirror_enabled:
    from birzha.application.home_market_mirror import HomeCanonicalMirror
    from birzha.storage.native_canonical_sheets import NativeCanonicalSheets
    from birzha.storage.google_mirror_auth import GoogleRefreshToken
    _native_mirror = HomeCanonicalMirror(
        _historical_store,
        NativeCanonicalSheets(GoogleRefreshToken(settings.native_mirror_credentials_path)),
        settings.source_sha,
    )


_model3_archive = None
if _native_mirror is not None:
    from birzha.application.model3_archive import Model3ArchiveUpdater
    _model3_archive = Model3ArchiveUpdater(
        transport=_native_mirror.transport,
        minute_archive_dir=settings.model3_minute_archive_dir,
        audit_dir=settings.model3_audit_dir,
        source=_historical_store,
        source_sha=settings.source_sha or "UNKNOWN",
        lock=_native_mirror._lock,
    )


@mcp.tool(name="archive.update", description="Unified instrument Google archive; registered shared indicator sets are calculated independently of forecasts. RI: reconcile existing D1/H1/M15 and their INDICATORS tabs, restore missing M15 from configured saved M1, compute missing/dependent versioned Purnov research features, repair chronological row order, create local backup and verify all writes. Dry-run by default. Source collection is a separate archive.collect command; no new books, paid source or Forecast admission.",
          annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True))
def archive_update(from_date: str, till_date: str, dry_run: bool = True,
                   repair_dependent: bool = False, symbol: str = "RI") -> dict[str, object]:
    from birzha.application.instrument_archive_registry import archive_contract
    try:
        contract=archive_contract(symbol)
    except ValueError as exc:
        return {"status":"BLOCKED","reason":str(exc),"written":False}
    if contract["symbol"] != "RI":
        return {"status":"BLOCKED","reason":"indicator/archive policy not yet accepted for this instrument",
                "contract":contract,"written":False}
    if _model3_archive is None:
        return {"status":"BLOCKED","reason":"existing authenticated native mirror is not configured","written":False}
    try:
        from datetime import date as _date
        left,right=_date.fromisoformat(from_date),_date.fromisoformat(till_date)
        if not 0 <= (right-left).days < 367:
            raise ValueError("bounded exact date range required")
        return _model3_archive.update(from_date=from_date,till_date=till_date,dry_run=dry_run,
            repair_dependent=repair_dependent,exclusive_workbook_owner=settings.native_mirror_sole_writer)
    except Exception as exc:
        from birzha.application.canonical_market_mirror import MirrorConflict
        return {"status":"ERROR","reason":str(exc)[:200] if isinstance(exc,(MirrorConflict,ValueError)) else "SOURCE_OR_RUNTIME_FAILURE",
                "written":"UNKNOWN_RECONCILE_BEFORE_RETRY" if not dry_run else False}


@mcp.tool(name="archive.sync", description="RI 2026 unified Google-first archive: reuse saved candles/M1, official T-1 candidate ledger, fetch only wholly missing exact-contract day/timeframe windows with shared governor, shared indicators, fixed-anchor FORWARD_SPREAD continuous columns in existing INDICATORS, immutable RAW and readback. Preview default. Bounded source budget returns PARTIAL; repeat same arguments to resume. Historical research only; no Forecast admission or new sheets.",
          annotations=ToolAnnotations(read_only_hint=False,destructive_hint=False,idempotent_hint=True,open_world_hint=True))
def archive_sync(symbol: str, from_date: str, till_date: str, dry_run: bool = True,
                 repair_dependent: bool = False, max_source_windows: int = 6) -> dict[str, object]:
    if _native_mirror is None or _model3_archive is None:
        return {"status":"BLOCKED","reason":"authenticated Google archive writer is not configured","written":False}
    from birzha.application.archive_sync import ArchiveSync
    try:
        return ArchiveSync(_native_mirror.transport,_market,_model3_archive,
            settings.model3_audit_dir,_native_mirror._lock).sync(
                symbol=symbol,from_date=from_date,till_date=till_date,dry_run=dry_run,
                repair_dependent=repair_dependent,max_source_windows=max_source_windows,
                exclusive_workbook_owner=settings.native_mirror_sole_writer)
    except Exception as exc:
        return {"status":"ERROR","reason":str(exc)[:200] if isinstance(exc,(ValueError,RuntimeError)) else "SOURCE_OR_RUNTIME_FAILURE",
                "written":False if dry_run else "UNKNOWN_RECONCILE_BEFORE_RETRY"}


@mcp.tool(name="archive.collect", description="Model-independent Google-first collection for one exact contract and timeframe, at most three civil days. Reuse matching source receipts; refuse to re-download populated unproven windows. Existing governor, native D1/H1 and existing M1-to-M15 aggregator. Preview by default.",
          annotations=ToolAnnotations(read_only_hint=False,destructive_hint=False,idempotent_hint=True,open_world_hint=True))
def archive_collect(symbol: str, exact_secid: str, timeframe: str, from_date: str, till_date: str, dry_run: bool = True) -> dict[str, object]:
    if _native_mirror is None:
        return {"status":"BLOCKED","reason":"authenticated Google archive writer is not configured","written":False}
    from birzha.application.archive_collection import ArchiveCollector
    try:
        return ArchiveCollector(_native_mirror.transport,_market,settings.model3_audit_dir,_native_mirror._lock).collect(
            symbol=symbol,exact_secid=exact_secid,timeframe=timeframe,from_date=from_date,till_date=till_date,
            dry_run=dry_run,exclusive_workbook_owner=settings.native_mirror_sole_writer)
    except Exception as exc:
        return {"status":"ERROR","reason":str(exc)[:200],"written":False if dry_run else "UNKNOWN_RECONCILE_BEFORE_RETRY"}


@mcp.tool(name="archive.contract_plan", description="Use the existing historical futures resolver to plan exact contracts for a bounded range. No candles, Google writes or model predictions.",
          annotations=ToolAnnotations(read_only_hint=True,destructive_hint=False,idempotent_hint=True,open_world_hint=True))
def archive_contract_plan(symbol: str, from_date: str, till_date: str) -> dict[str, object]:
    from datetime import date
    from birzha.application.instrument_archive_registry import archive_contract
    try:
        archive_contract(symbol)
        a,b=date.fromisoformat(from_date),date.fromisoformat(till_date)
        if not 0 <= (b-a).days < 367:raise ValueError("date range exceeds 367 days")
        segments=_history._segments(symbol,a,b)
        return {"symbol":symbol,"contracts":[{"secid":i.secid,"from_date":left.isoformat(),"till_date":right.isoformat()} for i,left,right in segments],
            "selection":"EXISTING_HISTORICAL_RESOLVER_T_MINUS_1","historical_first_available":"UNVERIFIED","written":False}
    except Exception as exc:return {"status":"ERROR","reason":str(exc)[:200],"written":False}


@mcp.tool(name="archive.contract", description="Read the fixed instrument Google archive, candle routes and registered shared indicator sets; no network or writes.",
          annotations=ToolAnnotations(read_only_hint=True,destructive_hint=False,idempotent_hint=True,open_world_hint=False))
def archive_contract_tool(symbol: str) -> dict[str, object]:
    from birzha.application.instrument_archive_registry import archive_contract
    try:return archive_contract(symbol)
    except ValueError as exc:return {"status":"BLOCKED","reason":str(exc)}


@mcp.tool(name="mirror.status", description="Read local mirror activation settings without reading credentials or calling Google/MOEX.")
def mirror_status() -> dict[str, object]:
    from pathlib import Path
    return {"enabled":settings.native_mirror_enabled,"configured_in_process":_native_mirror is not None,
            "credentials_file_present":bool(settings.native_mirror_credentials_path and Path(settings.native_mirror_credentials_path).is_file()),
            "source_sha":settings.source_sha,"source_backend":settings.state_backend,
            "single_writer_required":True,"single_writer_declared":settings.native_mirror_sole_writer,
            "google_access_verified":False,"note":"Local settings are not a live Google authorisation check"}


@mcp.tool(name="mirror.sync", description="Read already stored data and reconcile one fixed canonical Google workbook. Dry-run by default; no MOEX requests or source database writes.")
def mirror_sync(symbol: str, from_date: str, till_date: str, dry_run: bool = True) -> dict[str, object]:
    if symbol.strip().upper() == "RI":
        return archive_update(from_date,till_date,dry_run=dry_run,symbol="RI")
    if _native_mirror is None:
        return {"status":"BLOCKED","reason":"background Google mirror is not configured","written":False}
    try:
        return _native_mirror.sync(symbol=symbol,from_date=from_date,till_date=till_date,dry_run=dry_run,exclusive_workbook_owner=settings.native_mirror_sole_writer)
    except Exception as exc:
        from birzha.application.canonical_market_mirror import MirrorConflict
        return {"status":"ERROR","symbol":symbol,"error_type":type(exc).__name__,
                "reason":str(exc)[:200] if isinstance(exc,MirrorConflict) else "SOURCE_OR_RUNTIME_FAILURE",
                "written":"UNKNOWN_RECONCILE_BEFORE_RETRY" if not dry_run else False}


@mcp.tool(name="mirror.sync_core", description="Mirror already stored HOME candles and service evidence into fixed canonical Google books. Dry-run by default; never contacts MOEX or changes source history, Forecast or Outcome.")
def mirror_sync_core(from_date: str, till_date: str, dry_run: bool = True) -> dict[str, object]:
    if _native_mirror is None:
        return {"status":"BLOCKED","reason":"background Google mirror is not configured","written":False}
    results=[]
    for symbol in CORE_HISTORY_SYMBOLS:
        try:
            result=_native_mirror.sync(symbol=symbol,from_date=from_date,till_date=till_date,dry_run=dry_run,exclusive_workbook_owner=settings.native_mirror_sole_writer)
            results.append(result)
        except Exception as exc:
            results.append({"symbol":symbol,"status":"ERROR","error_type":type(exc).__name__})
    success="MIRROR_DRY_RUN_READY" if dry_run else "MIRROR_SYNC_PASS"
    passed=sum(x.get("status")==success for x in results)
    return {"status":"PASS" if passed==len(results) else "PARTIAL","dry_run":dry_run,"requested":len(results),"passed":passed,"failed":len(results)-passed,"results":results}


@mcp.tool(name="system.version", description="Return BIRZHA MCP service version metadata.")
def system_version() -> dict[str, str]:
    return {"service": SERVICE_NAME, "version": VERSION, "architecture": ARCHITECTURE_VERSION}


@mcp.tool(name="market.resolve_instrument", description="Resolve a directly listed MOEX instrument such as SBER or the current liquid futures contract for a root such as Si.")
def market_resolve_instrument(symbol: str) -> dict[str, object]:
    return _market.resolve(symbol).to_dict()


@mcp.tool(name="market.resolve_active_future", description="Backward-compatible resolver for the current liquid MOEX futures root such as Si.")
def market_resolve_active_future(symbol: str) -> dict[str, object]:
    return _market.provider.resolve_active_future(symbol).to_dict()


@mcp.tool(name="market.candles", description="Load real MOEX candles for a resolved futures or equity instrument. Supported timeframes: M1, M10, M15, H1, D1, W1, MN1.")
def market_candles(symbol: str, timeframe: str, from_date: str, till_date: str, completed_only: bool = True) -> dict[str, object]:
    return _market.candles(symbol, timeframe=timeframe, from_date=from_date, till_date=till_date, completed_only=completed_only).to_dict()


@mcp.tool(name="market.recent_candles", description="Load recent real MOEX candles by lookback days for a supported instrument.")
def market_recent_candles(symbol: str, timeframe: str, lookback_days: int = 30, completed_only: bool = True) -> dict[str, object]:
    return _market.recent_candles(symbol, timeframe=timeframe, lookback_days=lookback_days, completed_only=completed_only).to_dict()


@mcp.tool(name="history.sync", description="Persist and repair historical MOEX candles for an instrument and timeframe. Only missing official exchange sessions are fetched; futures roots are split by the historically liquid real contract.")
def history_sync(symbol: str, timeframe: str, from_date: str, till_date: str) -> dict[str, object]:
    try:
        payload = _history.sync(symbol, timeframe=timeframe, from_date=from_date, till_date=till_date).to_dict()
        return {"status": "PASS", **payload}
    except Exception as exc:  # structured operational diagnostic; never includes credentials
        return {
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1500],
            "symbol": symbol,
            "timeframe": timeframe,
            "from_date": from_date,
            "till_date": till_date,
        }


@mcp.tool(name="history.sync_batch", description="Sequentially sync several instruments and timeframes; one failure does not stop the remaining history jobs.")
def history_sync_batch(symbols: list[str], timeframes: list[str], from_date: str, till_date: str) -> dict[str, object]:
    return _history.sync_many(symbols, timeframes, from_date=from_date, till_date=till_date)


@mcp.tool(name="history.sync_core", description="Sync the BIRZHA core research universe on D1/H1/M15, reusing already verified stored history.")
def history_sync_core(from_date: str, till_date: str) -> dict[str, object]:
    return _history.sync_many(CORE_HISTORY_SYMBOLS, CORE_HISTORY_TIMEFRAMES, from_date=from_date, till_date=till_date)


@mcp.tool(name="history.flow_sync", description="Persist TradeStats Delta and applicable FUTOI history so repeated forecasts and validation can reuse stored analytical data.")
def history_flow_sync(symbol: str, from_date: str, till_date: str) -> dict[str, object]:
    try:
        return {"status":"PASS", **_historical_flow.sync(symbol, from_date=from_date, till_date=till_date)}
    except Exception as exc:
        return {"status":"ERROR","symbol":symbol,"from_date":from_date,"till_date":till_date,"error_type":type(exc).__name__,"error":str(exc)[:1500]}


@mcp.tool(
    name="history.flow_capture_public_trades",
    description=(
        "Capture currently available public MOEX futures or equity trades for one symbol, "
        "derive TradeStats-compatible 5-minute buy/sell volume and price rows with optional value/open interest, "
        "and persist them without claiming unavailable historical completeness."
    ),
)
def history_flow_capture_public_trades(symbol: str) -> dict[str, object]:
    try:
        return {
            "status": "PASS",
            **_historical_flow.capture_public_recent_tradestats(symbol),
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "symbol": symbol,
            "error_type": type(exc).__name__,
            "error": str(exc)[:1500],
        }


@mcp.tool(name="history.session_rules", description="Read mandatory operating rules before downloading or concatenating D1/H1/M15 session data: raw prices, exact contracts, source loading, storage/Google book IDs, missing-data policy and autonomous execution. Reports installed source SHA; does not grant access or forecast admission.", annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
def history_session_rules() -> dict[str, object]:
    from birzha.application.session_data_rules import session_data_rules
    return {**session_data_rules(), "installed_source_sha": settings.source_sha}


@mcp.tool(name="history.stored_series", description="Read existing D1/H1/M15 bars for one exact SECID across session boundaries without network or source writes. Prices, gaps, NULLs and completion flags are preserved. This is inspection only, not calendar/PIT attestation or Forecast admission. Futures require an exact contract, not a root.", annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
def history_stored_series(secid: str, timeframe: str, from_date: str, till_date: str) -> dict[str, object]:
    from birzha.application.stored_session_series import stored_session_series, StoredSessionSeriesError
    try:
        return stored_session_series(_history, secid=secid, timeframe=timeframe,
                                     from_date=from_date, till_date=till_date)
    except StoredSessionSeriesError as exc:
        return {"status": "ERROR", "reason": str(exc)}
    except Exception:
        return {"status": "ERROR", "reason": "STORED_SERIES_READ_FAILED"}


@mcp.tool(name="history.br_research_reconcile", description="Bounded administrator repair for BR only. SHA-pinned Google research proposal, dry-run by default. Apply creates a same-database research quarantine with backup; never changes accepted candles, verified ranges or Forecast inputs. Requires owner-authorised write scope.", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
def history_br_research_reconcile(payload: dict, expected_sha256: str, apply: bool = False) -> dict:
    from birzha.application.br_research_repair import reconcile, RepairError
    try:
        return reconcile(_historical_store, payload, expected_sha256, apply=apply)
    except RepairError as exc:
        return {"status": "ERROR", "reason": str(exc)}
    except Exception:
        return {"status": "ERROR", "reason": "BR_RESEARCH_REPAIR_FAILED"}


@mcp.tool(name="history.br_research_series", description="Read BR research quarantine by exact contract and bounded dates. Original source fields preserved; never a verified calendar, historical first-seen or Forecast admission. Separate from the accepted historical reader.", annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False))
def history_br_research_series(secid: str, timeframe: str, from_date: str, till_date: str) -> dict:
    from birzha.application.br_research_repair import research_series, RepairError
    try:
        return research_series(_historical_store, secid, timeframe, from_date, till_date)
    except (RepairError, ValueError) as exc:
        return {"status": "ERROR", "reason": str(exc) if isinstance(exc, RepairError) else "INVALID_DATE_RANGE"}
    except Exception:
        return {"status": "ERROR", "reason": "BR_RESEARCH_READ_FAILED"}

@mcp.tool(name="history.coverage", description="Return durable stored coverage for one exact MOEX SECID and timeframe.")
def history_coverage(secid: str, timeframe: str) -> dict[str, object]:
    try:
        return {"status": "PASS", **_historical_store.coverage(secid, timeframe).to_dict()}
    except Exception as exc:
        return {
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1500],
            "secid": secid,
            "timeframe": timeframe,
        }


@mcp.tool(name="market.flow", description="Build real MOEX flow analytics from ALGOPACK TradeStats and, for futures, FUTOI: aggressive buy/sell volume, volume delta, value delta and open interest.")
def market_flow(symbol: str, from_date: str | None = None, till_date: str | None = None, lookback_days: int = 5) -> dict[str, object]:
    return _flow.build(symbol, from_date=from_date, till_date=till_date, lookback_days=lookback_days).to_dict()


@mcp.tool(name="market.snapshot", description="Build a causal D1/H1/M15 Market Snapshot from real MOEX price, volume, ALGOPACK Delta and applicable OI data at one forecast T0.")
def market_snapshot(symbol: str, as_of_date: str | None = None) -> dict[str, object]:
    return _snapshot.build(symbol, as_of_date=as_of_date).to_dict()


@mcp.tool(
    name="prediction.contract",
    description="Build the formal Protocol 08 / MASTER ROADMAP Stage D Prediction Contract for one market at causal T0.",
)
def prediction_contract(symbol: str, as_of_date: str | None = None) -> dict[str, object]:
    return _prediction_contract.build(symbol, as_of_date=as_of_date).to_dict()


@mcp.tool(
    name="outcome.contract",
    description=(
        "Evaluate the formal Protocol 08 first-touch Outcome Contract for a "
        "Prediction Contract: UP_FIRST / DOWN_FIRST / NEITHER / AMBIGUOUS, "
        "hit timing and direction-neutral MFE/MAE."
    ),
)
def outcome_contract(
    symbol: str,
    as_of_date: str | None = None,
    evaluation_date: str | None = None,
) -> dict[str, object]:
    return _outcome_contract.evaluate(
        symbol,
        as_of_date=as_of_date,
        evaluation_date=evaluation_date,
    ).to_dict()



@mcp.tool(
    name="data.forecast_input_readiness",
    description="Audit implementation readiness of Protocol 08 forecast inputs for one supported market. Reports READY/PARTIAL/MISSING/NOT_APPLICABLE without claiming historical coverage.",
)
def data_forecast_input_readiness(symbol: str) -> dict[str, object]:
    return _forecast_input_readiness.audit(symbol)


@mcp.tool(
    name="data.forecast_input_readiness_core",
    description="Audit Protocol 08 forecast-input implementation readiness for all six BIRZHA core markets.",
)
def data_forecast_input_readiness_core() -> dict[str, object]:
    reports = {
        symbol: _forecast_input_readiness.audit(symbol)
        for symbol in CORE_HISTORY_SYMBOLS
    }
    return {
        "schema": "FORECAST_INPUT_READINESS_CORE_V1",
        "markets": reports,
        "ready_markets": [
            symbol
            for symbol, report in reports.items()
            if report["ready_for_protocol_08"]
        ],
    }



@mcp.tool(
    name="data.flow_storage_coverage",
    description="Summarize locally stored flow coverage by dataset/key without fetching MOEX data.",
)
def data_flow_storage_coverage(dataset: str | None = None) -> dict[str, object]:
    coverage = getattr(_historical_flow_store, "coverage", None)
    if coverage is None:
        return {
            "schema": "FLOW_STORAGE_COVERAGE_V1",
            "storage_scope": getattr(_historical_flow_store, "storage_scope", "unknown"),
            "status": "UNSUPPORTED",
            "items": [],
        }
    items = coverage(dataset)
    return {
        "schema": "FLOW_STORAGE_COVERAGE_V1",
        "storage_scope": getattr(_historical_flow_store, "storage_scope", "unknown"),
        "status": "PASS",
        "dataset": dataset,
        "items": items,
    }


@mcp.tool(
    name="data.flow_backfill_causal_availability",
    description="Dry-run or apply a safe local backfill of inferred availability timestamps for stored TRADESTATS and PUBLIC_TRADES_RAW rows only. FUTOI is never modified.",
)
def data_flow_backfill_causal_availability(dry_run: bool = True) -> dict[str, object]:
    backfill = getattr(_historical_flow_store, "backfill_causal_availability", None)
    if backfill is None:
        return {
            "schema": "FLOW_CAUSAL_BACKFILL_V1",
            "status": "UNSUPPORTED",
            "dry_run": dry_run,
        }
    return {
        "status": "PASS",
        **backfill(dry_run=dry_run),
    }


@mcp.tool(name="forecast.build", description="Build an explainable ex-ante BIRZHA baseline forecast without persistence. Use forecast.create for an operational forecast that must enter the journal.")
def forecast_build(symbol: str, as_of_date: str | None = None) -> dict[str, object]:
    return _forecast.build(symbol, as_of_date=as_of_date).to_dict()


@mcp.tool(name="forecast.create", description="Create an ex-ante forecast and append it immutably to the configured Forecast Journal. Identical duplicate writes are idempotent; conflicting content is rejected.")
def forecast_create(symbol: str, as_of_date: str | None = None) -> dict[str, object]:
    return _journal.create_and_save(symbol, as_of_date=as_of_date)


@mcp.tool(name="forecast.get", description="Read one immutable Forecast Record from the Forecast Journal by forecast_id.")
def forecast_get(forecast_id: str) -> dict[str, object]:
    record = _journal.get(forecast_id)
    return record if record is not None else {"forecast_id": forecast_id, "status": "NOT_FOUND"}


@mcp.tool(name="forecast.list", description="List recent immutable Forecast Records, optionally filtered by symbol.")
def forecast_list(limit: int = 20, symbol: str | None = None) -> dict[str, object]:
    return _journal.list_recent(limit=limit, symbol=symbol)


@mcp.tool(name="analysis.run_core", description="When the user asks to analyze the market, verify and refresh recent completed daily history, then create and save a preliminary forecast for each of the six configured instruments. Return a plain Russian summary and per-instrument errors. This does not certify the full archive or model accuracy.")
def analysis_run_core() -> dict[str, object]:
    return _analysis.run()


@mcp.tool(name="outcome.evaluate", description="Evaluate a stored forecast against future completed MOEX trading sessions and append newly matured 5/10/20-session outcomes without modifying the forecast.")
def outcome_evaluate(forecast_id: str, evaluation_date: str | None = None) -> dict[str, object]:
    return _outcomes.evaluate(forecast_id, evaluation_date=evaluation_date).to_dict()


@mcp.tool(name="outcome.list", description="List append-only matured outcomes already recorded for one forecast.")
def outcome_list(forecast_id: str) -> dict[str, object]:
    records = _outcome_store.list_for_forecast(forecast_id)
    return {"forecast_id": forecast_id, "count": len(records), "outcomes": [item.to_dict() for item in records]}


@mcp.tool(name="validation.walk_forward", description="Run a causal historical walk-forward validation over official MOEX trading sessions. Historical futures roots are resolved to the contract that was liquid on each forecast date.")
def validation_walk_forward(symbol: str, start_date: str, end_date: str, step_sessions: int = 5, max_points: int = 24) -> dict[str, object]:
    return _validator.run(symbol, start_date=start_date, end_date=end_date, step_sessions=step_sessions, max_points=max_points).to_dict()


@mcp.tool(name="validation.methods_walk_forward", description="Run causal walk-forward validation for independent directional forecast methods on stored historical data. Does not open holdout or tune ensemble weights.")
def validation_methods_walk_forward(symbol: str, start_date: str, end_date: str, step_sessions: int = 5, max_points: int = 60) -> dict[str, object]:
    return _method_validator.run(
        symbol,
        start_date=start_date,
        end_date=end_date,
        step_sessions=step_sessions,
        max_points=max_points,
    ).to_dict()


@mcp.tool(name="validation.assess_model", description="Statistically assess the current Forecast Engine on a causal walk-forward sample. Requires enough observations, sufficient directional coverage and a 95% Wilson lower bound above random 50% direction accuracy before ACCEPTED.")
def validation_assess_model(symbol: str, start_date: str, end_date: str, step_sessions: int = 5, max_points: int = 60) -> dict[str, object]:
    return _model_lab.assess(
        symbol,
        start_date=start_date,
        end_date=end_date,
        step_sessions=step_sessions,
        max_points=max_points,
    ).to_dict()


@mcp.tool(name="validation.development_holdout", description="Evaluate the current model separately on a development period and a later untouched holdout period. Overall ACCEPTED requires both periods to pass the existing statistical gate.")
def validation_development_holdout(symbol: str, development_start: str, split_date: str, holdout_end: str, step_sessions: int = 5, max_points: int = 60) -> dict[str, object]:
    return _model_lab.assess_development_holdout(
        symbol,
        development_start=development_start,
        split_date=split_date,
        holdout_end=holdout_end,
        step_sessions=step_sessions,
        max_points=max_points,
    ).to_dict()


@mcp.tool(name="validation.calibrate_model", description="Choose forecast settings on a development period only, then evaluate the selected settings once on a later untouched holdout period. Acceptance thresholds are never weakened.")
def validation_calibrate_model(symbol: str, development_start: str, split_date: str, holdout_end: str, step_sessions: int = 5, max_points: int = 60) -> dict[str, object]:
    return _calibration.calibrate(
        symbol,
        development_start=development_start,
        split_date=split_date,
        holdout_end=holdout_end,
        step_sessions=step_sessions,
        max_points=max_points,
    ).to_dict()


@mcp.tool(name="validation.calibrate_core", description="Select one forecast configuration across the core BIRZHA research universe on development data, then test that unchanged configuration on later holdout data for every symbol.")
def validation_calibrate_core(development_start: str, split_date: str, holdout_end: str, step_sessions: int = 5, max_points: int = 60) -> dict[str, object]:
    return calibrate_across_symbols(
        _calibration, tuple(CORE_HISTORY_SYMBOLS),
        development_start=development_start, split_date=split_date, holdout_end=holdout_end,
        step_sessions=step_sessions, max_points=max_points,
    ).to_dict()


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "service": SERVICE_NAME,
        "version": VERSION,
        "state_backend": settings.state_backend,
        "historical_storage": _historical_store.storage_scope,
    })


transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=list(settings.mcp_allowed_hosts),
    allowed_origins=list(settings.mcp_allowed_origins),
)

app = mcp.streamable_http_app(json_response=True, transport_security=transport_security)
if settings.mcp_bearer_token:
    app.add_middleware(McpBearerAuthMiddleware, token=settings.mcp_bearer_token)


@mcp.tool(name="market.continuous_series", description="Read-only RI multi-contract coordinates from supplied exact-contract bars and T-1 ledger evidence. No source fetching, storage writes, calendar/PIT attestation or forecast admission.")
def market_continuous_series(payload: dict[str, object]) -> dict[str, object]:
    from birzha.application.continuous_series import build_continuous_series
    return build_continuous_series(payload)
