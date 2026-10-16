"""Integrated HOME mirror: stored source -> existing schemas -> append/readback -> status.

The source is an existing live store, not a second DuckDB connection. This service
never refreshes source data, constructs forecasts, or promotes calendar/PIT proof.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
import threading
from zoneinfo import ZoneInfo

from birzha.application.canonical_market_mirror import CanonicalMirrorPublisher, MirrorConflict, digest, rectangular, index_rows, plan_append, sheet_timestamp, validate_identity
from birzha.application.market_mirror import D1_COLUMNS
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge

MOSCOW = ZoneInfo("Europe/Moscow")
EXTRA_COLUMNS = ["research_status", "normalized_sha256", "source_archive_sha256", "capture_day_status", "source_kind", "caveat"]


def _stamp(value: object) -> datetime:
    parsed = sheet_timestamp(value)
    # Explicit provider clock interpretation only; not a first-availability proof.
    return parsed.replace(tzinfo=MOSCOW) if parsed.tzinfo is None else parsed.astimezone(MOSCOW)


def project_source(bundle: dict, timeframe: str, existing: list[list[object]]) -> tuple[list[list[object]], dict]:
    before = rectangular(existing) if existing else []
    header = before[0] if before else D1_COLUMNS + (EXTRA_COLUMNS if timeframe != "D1" else [])
    if before:
        validate_identity(before, bundle["symbol"], timeframe, incoming=False)
    original = index_rows(before) if before else {}
    by_identity = {}
    for row in original.values():
        identity = (row[header.index("contract_code")], _stamp(row[header.index("bar_start_time")]) )
        if identity in by_identity:
            raise MirrorConflict("destination has duplicate semantic candle identities")
        by_identity[identity] = row
    output = [header[:]]
    matches = 0
    differing_metadata = 0
    preserved_stricter_completion = 0
    preserved_unknown_index_volume = 0
    seen = {}
    identical_time_aliases = 0
    for candle in bundle["candles"]:
        if candle["timeframe"] != timeframe:
            continue
        secid, start = candle["secid"], _stamp(candle["begin"])
        identity = (secid, start)
        comparable = {**candle, "begin": start.isoformat(), "end_time": _stamp(candle["end_time"]).isoformat()}
        if identity in seen:
            if seen[identity] != comparable:
                raise MirrorConflict("source has conflicting semantic candle identities")
            identical_time_aliases += 1
            continue
        seen[identity] = comparable
        old = by_identity.get(identity)
        if old is not None:
            # Preserve older provenances, key spellings and accepted prices.
            # Never reinterpret differing metadata as a source payload match.
            for column, key in (("open","open"),("high","high"),("low","low"),("close","close"),("volume","volume")):
                value = candle[key]
                prior = old[header.index(column)]
                if (column == "volume" and bundle["symbol"] in {"IMOEX", "RTSI"}
                        and prior == "" and value == 0 and not isinstance(value, bool)):
                    # Preserve explicitly missing index volume; never call NULL
                    # equal to zero or replace it with the provider placeholder.
                    preserved_unknown_index_volume += 1
                    continue
                if value is None or prior != value:
                    raise MirrorConflict("stored source conflicts with existing candle values")
            if _stamp(old[header.index("bar_end_time")]) != _stamp(candle["end_time"]):
                raise MirrorConflict("stored source conflicts with existing interval or source completion")
            prior_completion = old[header.index("is_complete")]
            if prior_completion is not candle["completed"]:
                if prior_completion is False and candle["completed"] is True:
                    # Completed source interval does not attest minute coverage.
                    # Keep the stricter archived false, report the difference.
                    preserved_stricter_completion += 1
                else:
                    raise MirrorConflict("stored source conflicts with existing interval or source completion")
            if candle["value"] is not None and old[header.index("turnover")] != "" and old[header.index("turnover")] != candle["value"]:
                raise MirrorConflict("stored source conflicts with existing turnover")
            matches += 1
            if old[header.index("source_id")] != candle["source"]:
                differing_metadata += 1
            output.append(old[:])
            continue
        if any(candle[x] is None for x in ("open", "high", "low", "close", "volume")):
            raise MirrorConflict("new source candle lacks OHLCV; no imputation")
        begin = candle["begin"] if timeframe == "D1" else start.isoformat()
        end = candle["end_time"] if timeframe == "D1" else _stamp(candle["end_time"]).isoformat()
        source = {"record_key": f"{bundle['symbol']}|{timeframe}|{secid}|{begin}",
                  "calendar_date": start.date().isoformat(), "trade_session_date": "", "session_id": "",
                  "bar_start_time": begin, "bar_end_time": end, "available_at": "", "available_at_confidence": "UNKNOWN",
                  "contract_code": secid, "open": candle["open"], "high": candle["high"], "low": candle["low"],
                  "close": candle["close"], "volume": candle["volume"], "number_of_trades": "",
                  "turnover": "" if candle["value"] is None else candle["value"], "is_complete": candle["completed"],
                  "source_interval": timeframe, "source_candles": "", "source_id": candle["source"],
                  "research_status": "RESEARCH_ONLY_OPERATIONAL_MIRROR", "normalized_sha256": "",
                  "source_archive_sha256": "", "capture_day_status": "SOURCE_COMPLETENESS_NOT_ATTESTED",
                  "source_kind": "STORED_DUCKDB", "caveat": "PIT_NOT_ATTESTED;SESSION_UNVERIFIED;SOURCE_FLAGS_PRESERVED;PROVIDER_CLOCK_EUROPE_MOSCOW",
                  "source_capture_label": "stored_observed_at=" + str(candle.get("observed_at") or "UNKNOWN"),
                  "source_file_id": ""}
        unknown = set(header) - set(source)
        if unknown:
            raise MirrorConflict("source projection cannot populate unknown destination columns")
        output.append([source[x] for x in header])
    # Strict validation and detection of old-key conflicts before any writes.
    plan_append(symbol=bundle["symbol"], timeframe=timeframe, existing=existing, incoming=output)
    return output, {"matched_source_bars": matches, "identical_source_time_aliases": identical_time_aliases, "preserved_different_provenance": differing_metadata,
                    "preserved_stricter_completion": preserved_stricter_completion,
                    "preserved_unknown_index_volume": preserved_unknown_index_volume,
                    "full_source_payload_match_claimed": False,
                    "price_semantics": "NO_ADJUSTMENT", "timestamp_interpretation": "MOEX_PROVIDER_EUROPE_MOSCOW_NOT_PIT_PROOF"}


def append_service(existing: list[list[object]], header: list[str], incoming: list[list[object]]) -> list[list[object]]:
    if existing and existing[0] != header:
        raise MirrorConflict("service sheet schema mismatch")
    before = [list(row) for row in existing] if existing else [header[:]]
    if any(len(row) != len(header) for row in before):
        raise MirrorConflict("nonrectangular service sheet")
    identity = lambda row: (str(row[0]).upper(), *row[1:]) if header[0] == "symbol" else tuple(row)
    seen = set(identity(row) for row in before[1:])
    if len(seen) != len(before) - 1:
        raise MirrorConflict("duplicate service evidence rows")
    additions = [header[:]] if not existing else []
    for row in incoming:
        if len(row) != len(header):
            raise MirrorConflict("source service row width mismatch")
        key=identity(row)
        if key not in seen:
            additions.append(row[:]);seen.add(key)
    return additions


@dataclass
class HomeCanonicalMirror:
    source: object
    transport: object
    source_sha: str
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _service_rows(self, symbol: str, sheet: str, header: list[str], incoming: list[list[object]]) -> None:
        self.transport.validate_service_target(symbol, sheet)
        old = self.transport.read_service_rows(symbol, sheet)
        additions = append_service(old, header, incoming)
        if digest(self.transport.read_service_rows(symbol, sheet)) != digest(old):
            raise MirrorConflict("service sheet changed during preflight")
        for offset in range(0, len(additions), 500):
            chunk = additions[offset:offset+500]
            self.transport.append_service_rows(symbol, sheet, chunk)
            old += chunk
            if digest(self.transport.read_service_rows(symbol, sheet)) != digest(old):
                raise MirrorConflict("service evidence readback mismatch")

    def sync(self, *, symbol: str, from_date: str, till_date: str, dry_run: bool = True, exclusive_workbook_owner: bool = False) -> dict:
        symbol, _ = GoogleSheetsBridge.target_for_symbol(symbol)
        if not dry_run and exclusive_workbook_owner is not True:
            raise MirrorConflict("whole-workbook sole writer ownership required")
        if not self._lock.acquire(blocking=False):
            raise MirrorConflict("another mirror operation is active in this server")
        mutation_started = False
        try:
            bundle = self.source.export_mirror_bundle(symbol, from_date, till_date)
            if bundle["backend"] != "DuckDB" or bundle["symbol"] != symbol:
                raise MirrorConflict("unexpected source backend or market")
            prepared = {}
            for timeframe in ("D1", "H1", "M15"):
                self.transport.validate_target(symbol,timeframe)
                current = self.transport.read_rows(symbol,timeframe)
                incoming, evidence = project_source(bundle,timeframe,current)
                if len(incoming) == 1:
                    raise MirrorConflict(f"source has no {timeframe} candles in requested range")
                plan=plan_append(symbol=symbol,timeframe=timeframe,existing=current,incoming=incoming)
                prepared[timeframe]=(incoming,evidence,len(plan.additions)-int(plan.initialise_header),plan.additions[1:] if plan.initialise_header else plan.additions,plan.before_sha256)
            # Preflight service headers before data writes as well.
            services = {"SESSIONS": (["symbol","secid","trade_date"], bundle["sessions"]),
                        "VERIFIED_RANGES": (["scope","symbol","timeframe","from_date","till_date"],
                            [["DUCKDB_STORED_MARKER_NOT_INDEPENDENT_ATTESTATION:" + row[0], symbol, row[1], row[2], row[3]] for row in bundle["verified_ranges"]])}
            for sheet,(header,new_rows) in services.items():
                self.transport.validate_service_target(symbol,sheet)
                append_service(self.transport.read_service_rows(symbol,sheet),header,new_rows)
            self.transport.validate_service_target(symbol,"SYNC_STATUS")
            self.transport.read_status(symbol)  # validates keyed status schema and uniqueness
            if dry_run:
                return {"status":"MIRROR_DRY_RUN_READY", "symbol":symbol, "source_backend":"DuckDB",
                        "planned_additions":{tf:x[2] for tf,x in prepared.items()}, "written":False,
                        "evidence":{tf:x[1] for tf,x in prepared.items()}}
            mutation_started = True
            self.transport.update_status(symbol,{"status":"MIRROR_SYNC_IN_PROGRESS","mirror_v3_status":"IN_PROGRESS","mirror_v3_source_sha":self.source_sha})
            result={}
            publisher=CanonicalMirrorPublisher(self.transport)
            for tf,(incoming,evidence,_,missing,preflight_sha) in prepared.items():
                self.transport.validate_target(symbol,tf)
                current = self.transport.read_rows(symbol,tf)
                if digest(rectangular(current) if current else []) != preflight_sha:
                    raise MirrorConflict("candle sheet changed since whole-workbook preflight")
                # Process bounded subsets; existing rows are never removed by range batching.
                added_total = 0
                if not missing:
                    result[tf]=publisher.sync(symbol=symbol,timeframe=tf,incoming=[incoming[0]],exclusive_workbook_owner=True)
                for offset in range(0,len(missing),1500):
                    result[tf]=publisher.sync(symbol=symbol,timeframe=tf,incoming=[incoming[0],*missing[offset:offset+1500]],exclusive_workbook_owner=True)
                    added_total += result[tf]["added_rows"]
                result[tf]["added_rows"] = added_total
                result[tf]["evidence"]=evidence
            for sheet,(header,new_rows) in services.items():
                self._service_rows(symbol,sheet,header,new_rows)
            # Verify all three final sheets again before publishing a success marker.
            for tf in result:
                self.transport.validate_target(symbol,tf)
                if digest(rectangular(self.transport.read_rows(symbol,tf))) != result[tf]["sha256"]:
                    raise MirrorConflict("final cross-sheet digest mismatch")
            status={"status":"MIRROR_SYNC_PASS","primary_store":"DuckDB", "persistent_timeframe":"D1/H1/M15",
                    "intraday_mode":"STORED_OPERATIONAL_PLUS_PRESERVED_RESEARCH_ARCHIVE",
                    "sync_direction":"DuckDB -> Google Sheets", "mirror_v3_status":"PASS",
                    "mirror_v3_source_sha":self.source_sha,"mirror_v3_last_utc":datetime.now(ZoneInfo("UTC")).isoformat(),
                    "mirror_v3_calendar":"UNVERIFIED","mirror_v3_historical_pit":"UNVERIFIED"}
            for tf,summary in result.items():
                status[f"mirror_v3_{tf}_rows"]=summary["data_rows"]
                status[f"mirror_v3_{tf}_sha256"]=summary["sha256"]
            self.transport.update_status(symbol,status)
            return {"status":"MIRROR_SYNC_PASS", "symbol":symbol, "source_backend":"DuckDB", "sheets":result,
                    "calendar_attestation":"UNVERIFIED", "historical_pit":"UNVERIFIED"}
        except Exception:
            # Failure must never leave an old overall success marker after mutations.
            # No writes are performed in dry-run; failure status best-effort for write mode.
            if mutation_started:
                try: self.transport.update_status(symbol,{"status":"MIRROR_SYNC_FAILED","mirror_v3_status":"FAILED"})
                except Exception: pass
            raise
        finally:
            self._lock.release()