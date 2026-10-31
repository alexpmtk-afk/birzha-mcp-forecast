"""Model-independent, Google-first bounded source collection."""
from dataclasses import dataclass
from datetime import date,datetime,timezone
from pathlib import Path
import json,uuid
from birzha.application.canonical_market_mirror import MirrorConflict,CanonicalMirrorPublisher,digest,rectangular
from birzha.application.home_market_mirror import project_source,_stamp
from birzha.application.instrument_archive_registry import archive_contract

@dataclass
class ArchiveCollector:
    transport: object
    market: object
    audit_dir: str | None
    lock: object

    def collect(self,*,symbol,exact_secid,timeframe,from_date,till_date,dry_run=True,exclusive_workbook_owner=False):
        contract=archive_contract(symbol);symbol=contract["symbol"]
        left,right=date.fromisoformat(from_date),date.fromisoformat(till_date)
        if not 0<=(right-left).days<3 or timeframe not in ("D1","H1","M15"):raise MirrorConflict("one exact contract/timeframe and at most three civil days per source call")
        if not dry_run and not exclusive_workbook_owner:raise MirrorConflict("sole workbook writer required")
        if not self.lock.acquire(blocking=False):raise MirrorConflict("archive writer busy")
        started=False
        try:
            self.transport.validate_target(symbol,timeframe)
            old=rectangular(self.transport.read_rows(symbol,timeframe));h=old[0]
            existing=[r for r in old[1:] if r[h.index("contract_code")]==exact_secid and from_date<=_stamp(r[h.index("bar_start_time")]).date().isoformat()<=till_date]
            self.transport.validate_service_target(symbol,"SYNC_STATUS")
            state=dict(self.transport.read_status(symbol)[1:])
            receipt_key=f"archive_source|{exact_secid}|{timeframe}|{from_date}|{till_date}"
            receipt=state.get(receipt_key)
            if receipt:
                r=json.loads(receipt)
                if r.get("rows_sha256")!=digest(existing):raise MirrorConflict("source receipt no longer matches current Google rows")
                return {"status":"ARCHIVE_SOURCE_REUSED","symbol":symbol,"timeframe":timeframe,"rows":len(existing),"network_requests":0,"written":False}
            if existing:
                return {"status":"SOURCE_COVERAGE_UNKNOWN","symbol":symbol,"timeframe":timeframe,"rows_preserved":len(existing),"network_requests":0,"written":False,
                    "reason":"existing rows have no matching full-response receipt; do not re-download them or infer missing bars from row count"}
            if dry_run:return {"status":"ARCHIVE_COLLECTION_PLANNED","symbol":symbol,"exact_secid":exact_secid,"timeframe":timeframe,"from_date":from_date,"till_date":till_date,"written":False,"network_requests":0,"canonical_storage":"GOOGLE_SHEETS"}
            if not self.audit_dir:raise MirrorConflict("local audit directory required")
            if symbol=="RI" and timeframe=="M15" and getattr(getattr(self.market,"provider",None),"_minute_observer",None) is None:
                raise MirrorConflict("persistent RI M1 capture must be configured before M15 collection")
            # Exact instrument metadata is resolved by the existing official resolver.
            instrument=self.market.direct_resolver.resolve(exact_secid)
            if instrument is None or instrument.secid!=exact_secid:raise MirrorConflict("exact instrument metadata unavailable")
            # Validate book family before spending candle requests.
            from birzha.application.canonical_market_mirror import validate_identity
            validate_identity([["record_key","bar_start_time","contract_code"],[f"{symbol}|{timeframe}|{exact_secid}|2000-01-01","2000-01-01",exact_secid]],symbol,timeframe,incoming=False)
            if symbol=="RI" and (instrument.engine!="futures" or instrument.market!="forts"):raise MirrorConflict("RI must be a FORTS futures contract")
            series=self.market.candles_for_instrument(instrument,timeframe=timeframe,from_date=from_date,till_date=till_date,completed_only=False)
            if series.instrument.secid!=exact_secid or series.timeframe!=timeframe:raise MirrorConflict("provider identity mismatch")
            raw=[]
            for c in series.candles:
                if not from_date<=_stamp(c.begin).date().isoformat()<=till_date:raise MirrorConflict("source candle outside requested dates")
                raw.append(dict(secid=exact_secid,timeframe=timeframe,begin=c.begin,end_time=c.end,
                    open=c.open,high=c.high,low=c.low,close=c.close,volume=c.volume,value=c.value,completed=c.completed,
                    source=series.source,observed_at=datetime.now(timezone.utc).isoformat()))
            bundle=dict(symbol=symbol,candles=raw)
            incoming,_=project_source(bundle,timeframe,old)
            # Preserve source observations; the provider's existing M15 routine owns resampling.
            target=Path(self.audit_dir);target.mkdir(parents=True,exist_ok=True)
            token=uuid.uuid4().hex
            (target/(token+"_source.json")).write_text(json.dumps(dict(before=old,provider=raw),ensure_ascii=False,allow_nan=False),encoding="utf8")
            if digest(rectangular(self.transport.read_rows(symbol,timeframe)))!=digest(old):raise MirrorConflict("Google destination changed during collection")
            started=True
            self.transport.update_status(symbol,{"archive_status":"COLLECTION_IN_PROGRESS"})
            result=CanonicalMirrorPublisher(self.transport).sync(symbol=symbol,timeframe=timeframe,incoming=incoming,exclusive_workbook_owner=True)
            current=rectangular(self.transport.read_rows(symbol,timeframe))
            tc=current[0].index("bar_start_time");sc=current[0].index("contract_code")
            permutation=sorted(range(len(current)-1),key=lambda i:(_stamp(current[i+1][tc]),current[i+1][sc]))
            expected=[current[0],*[current[i+1] for i in permutation]]
            if expected!=current:self.transport.model3_order_rows(symbol,timeframe,permutation)
            after=rectangular(self.transport.read_rows(symbol,timeframe))
            if digest(after)!=digest(expected):raise MirrorConflict("ordered candle readback mismatch")
            selected=[r for r in after[1:] if r[sc]==exact_secid and from_date<=_stamp(r[tc]).date().isoformat()<=till_date]
            receipt=json.dumps(dict(rows_sha256=digest(selected),source_rows=len(raw),source="EXISTING_GOVERNED_PROVIDER",calendar="UNVERIFIED",historical_first_available="UNKNOWN"),separators=(",",":"))
            self.transport.update_status(symbol,{receipt_key:receipt,"archive_status":"CANDLES_READBACK_PASS"})
            return {**result,"status":"ARCHIVE_COLLECTION_READBACK_PASS","canonical_storage":"GOOGLE_SHEETS","forecast_admission":"NOT_GRANTED","source_rows":len(raw)}
        except Exception:
            if started:
                try:self.transport.update_status(symbol,{"archive_status":"FAILED_RECONCILE_BEFORE_RETRY"})
                except Exception:pass
            raise
        finally:self.lock.release()
