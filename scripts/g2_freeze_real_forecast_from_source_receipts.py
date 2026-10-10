"""Freeze uncalibrated REAL-RECEIPT baseline ForecastRecords in disposable staging.

Only original raw MOEX pages produced by G2 six-market governed collector can
feed this script. It never fetches the network itself, never uses reconstructed
historical development/holdout inputs, and never writes HOME/production.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import argparse
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from birzha.application.features import TimeframeFeatureEngine
from birzha.application.normalized_features import NormalizedFeatureEngine
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.prospective_capture import CaptureEvidence, ProspectivePilotLedger
from birzha.application.prospective_staging_bridge import CanonicalProspectiveStagingBridge
from birzha.domain.market import Instrument, Candle, CandleSeries
from birzha.domain.snapshot import MarketSnapshot, DataQualityContract, TimeframeQuality, market_snapshot_id
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal

VERSION = "G2_FIRST_REAL_SOURCE_BOUND_FORECAST_V1"
MOEX_TZ = ZoneInfo("Europe/Moscow")
EXPECTED_SOURCE = "G2_SIX_MARKET_LIVE_SOURCE_RECEIPT_V1"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def digest(content: bytes) -> str:
    return sha256(content).hexdigest()


def utc_text(dt):
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timezone required for immutable forecast T0")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value):
    dt=datetime.fromisoformat(str(value).replace("Z","+00:00").replace(" ","T"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp without explicit offset")
    return dt.astimezone(timezone.utc)


def moex_time(value):
    dt=datetime.fromisoformat(str(value).replace(" ","T"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MOEX_TZ).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def source_series(source_dir: Path, entry: dict, timeframe: str, instrument: Instrument):
    """Read all original pages and check source/receipt binding independently."""
    meta=entry["timeframes"][timeframe]
    all_candles=[]
    page_bodies=[]
    observed_times=[]
    for page in meta["pages"]:
        path=Path(page["path"])
        if path.name!=str(path) or not path.name.startswith(f"{entry['market']}_{instrument.secid}_{timeframe}_"):
            raise ValueError("malformed raw page path/SECID")
        body=(source_dir/path).read_bytes()
        if digest(body)!=page["sha256"]:
            raise ValueError("source SHA mismatch: "+path.name)
        seen=parse_time(page["observed_end_utc"])
        began=parse_time(page["observed_start_utc"])
        if began>seen:
            raise ValueError("reversed source receipt")
        payload=json.loads(body.decode("utf8"))
        table=payload.get("candles") or {}
        columns=table.get("columns") or []
        rows=table.get("data") or []
        if len(rows)!=page["row_count"]:
            raise ValueError("source row count changed after capture")
        for field in ("open","high","low","close","begin","end"):
            if field not in columns:
                raise ValueError("unverified raw candle field: "+field)
        for raw in rows:
            row=dict(zip(columns,raw))
            first=moex_time(row["begin"]);last=moex_time(row["end"])
            if not first < last <= seen:
                raise ValueError("raw candle not completed before observation")
            o,h,l,c=(float(row["open"]),float(row["high"]),float(row["low"]),float(row["close"]))
            if not all(math.isfinite(x) and x>0 for x in (o,h,l,c)) or h<max(o,c) or l>min(o,c):
                raise ValueError("invalid raw OHLC")
            all_candles.append(Candle(open=o,close=c,high=h,low=l,
                volume=float(row["volume"]) if row.get("volume") is not None else None,
                value=float(row["value"]) if row.get("value") is not None else None,
                begin=first.isoformat(),end=last.isoformat(),completed=True,
                observed_at=utc_text(seen),source="MOEX_ISS_REAL_CAPTURE"))
        page_bodies.append((path.name,body))
        observed_times.append(seen)
    if len(all_candles)!=meta["total_completed"]:
        raise ValueError("unverified source market/timeframe candle count")
    if not all_candles or [x.end for x in all_candles]!=sorted(set(x.end for x in all_candles)):
        raise ValueError("missing or duplicate market candles")
    if entry["resolved_secid"]!=instrument.secid or meta["exact_secid"]!=instrument.secid:
        raise ValueError("future/root SECID mismatch")
    return CandleSeries(instrument=instrument,timeframe=timeframe,candles=tuple(all_candles)),page_bodies,max(observed_times)


def contiguous_m15(m1: CandleSeries):
    groups=defaultdict(list)
    for candle in m1.candles:
        begin=datetime.fromisoformat(candle.begin).astimezone(MOEX_TZ)
        floor=begin.replace(minute=(begin.minute//15)*15,second=0,microsecond=0)
        groups[floor].append((begin,candle))
    verified=[]
    discarded=0
    for floor,pairs in sorted(groups.items()):
        pairs=sorted(pairs,key=lambda x:x[0])
        expected=[floor+timedelta(minutes=i) for i in range(15)]
        if len(pairs)!=15 or [x[0] for x in pairs]!=expected:
            discarded+=1
            continue
        bars=[x[1] for x in pairs]
        verified.append(Candle(open=bars[0].open,close=bars[-1].close,
             high=max(b.high for b in bars),low=min(b.low for b in bars),
             value=sum(b.value for b in bars) if all(b.value is not None for b in bars) else None,
             volume=sum(b.volume for b in bars) if all(b.volume is not None for b in bars) else None,
             begin=floor.astimezone(timezone.utc).isoformat(),end=bars[-1].end,
             completed=True,observed_at=bars[-1].observed_at,
             source="MOEX_ISS_REAL_CAPTURE_EXACT_15xM1"))
    return CandleSeries(instrument=m1.instrument,timeframe="M15",candles=tuple(verified)),discarded


def assemble_snapshot(entry, source_dir: Path, *, clock):
    if entry["status"]!="SOURCE_CAPTURE_COMPLETE":
        raise ValueError("refusing incomplete market source")
    instrument=Instrument(**entry["instrument"])
    evidence={}
    all_bodies=[]
    times=[]
    for tf in ("D1","H1","M1"):
        series,parts,seen=source_series(source_dir,entry,tf,instrument)
        evidence[tf]=series
        all_bodies.extend(parts)
        times.append(seen)
    m15,skipped=contiguous_m15(evidence["M1"])
    evidence["M15"]=m15
    counts={tf:evidence[tf].count for tf in ("D1","H1","M15")}
    if any(v<50 for v in counts.values()):
        raise ValueError("insufficient actual completed bars for first real forecast: "+str(counts))
    now=clock()
    decision=parse_time(utc_text(now))
    if decision < max(times) or (decision-max(times)).total_seconds()>300:
        raise ValueError("first forecast T0 must immediately follow original observed source")
    for series in (evidence["D1"], evidence["H1"],m15):
        if any(parse_time(c.end)>decision for c in series.candles):
            raise ValueError("snapshot contains future/unfinished candle")
    bundle=b"G2_RAW_MULTI_FRAME_SOURCE_BUNDLE_V1\n"
    for name,body in sorted(all_bodies):
        marker=name.encode("utf8")
        bundle+=len(marker).to_bytes(4,"big")+marker+len(body).to_bytes(8,"big")+body
    source_sha=digest(bundle)
    engine=TimeframeFeatureEngine()
    d1,h1,m15state=(engine.build(evidence[tf]) for tf in ("D1","H1","M15"))
    warnings=("public_price_only_no_authenticated_flow",
              "exchange_session_calendar_not_independently_verified",
              f"source_raw_bundle_sha256={source_sha}",
              f"m15_incomplete_buckets_excluded={skipped}")
    snap=MarketSnapshot(
        symbol=entry["market"],secid=instrument.secid,
        as_of=utc_text(decision),source="MOEX_ISS_GOVERNED_OBSERVED_INPUT",
        d1=d1,h1=h1,m15=m15state,data_quality="DEGRADED",warnings=warnings,
        quality_contract=DataQualityContract(
            version="DATA_QUALITY_CONTRACT_V2",status="DEGRADED",
            timeframes=tuple(TimeframeQuality(timeframe=tf,candles=counts[tf],
                minimum_required=50,latest_completed_end=evidence[tf].candles[-1].end,
                status="PASS") for tf in ("D1","H1","M15")),
            flow_status="NOT_REQUESTED",reasons=warnings),
        normalized_features=NormalizedFeatureEngine().build(
            d1=d1,h1=h1,m15=m15state,flow=None,volume_profile=None),
    )
    last_completed=max(parse_time(evidence[tf].candles[-1].end) for tf in ("D1","H1","M15"))
    return snap,bundle,utc_text(max(times)),utc_text(last_completed),counts,skipped


def freeze_from_receipts(source_dir: Path, output_dir: Path, *, clock=lambda:datetime.now(timezone.utc)):
    source_dir=Path(source_dir);output_dir=Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("refusing overwrite of frozen real forecast evidence")
    manifest_bytes=(source_dir/"manifest.json").read_bytes()
    manifest=json.loads(manifest_bytes)
    if manifest.get("schema")!=EXPECTED_SOURCE or manifest.get("origin")!="REAL_MOEX_PUBLIC_ISS_CAPTURE":
        raise ValueError("missing verified real-source manifest")
    listed={x["name"]:x["sha256"] for x in manifest["all_payload_files"]}
    for name,expected in listed.items():
        p=Path(name)
        if p.name!=name or digest((source_dir/p).read_bytes())!=expected:
            raise ValueError("manifest/raw original mismatch")
    output_dir.mkdir(parents=True,exist_ok=True)
    results=[]
    for entry in manifest["instruments"]:
        market=entry["market"]
        try:
            snap,bundle,observed,last_completed,counts,skipped=assemble_snapshot(entry,source_dir,clock=clock)
            record=build_forecast_from_snapshot(snap)
            if record.snapshot_id!=market_snapshot_id(snap) or record.created_at_t0!=snap.as_of:
                raise ValueError("snapshot/forecast identity not bound")
            root=output_dir/market
            root.mkdir(parents=True,exist_ok=False)
            pilot=ProspectivePilotLedger(root/"pilot.sqlite3",clock=clock)
            canonical= DuckDBForecastJournal(str(root/"forecast.duckdb"))
            outcomes= DuckDBOutcomeJournal(str(root/"outcome.duckdb"))
            try:
                bridge=CanonicalProspectiveStagingBridge(pilot,canonical,outcomes,root)
                receipt=bridge.capture(record,CaptureEvidence(
                    source_payload=bundle,source_observed_at=observed,
                    latest_completed_event_end=last_completed,
                    source_origin="LIVE_CAPTURED_PAYLOAD",
                    contract_version=snap.contract_version))
                if bridge.reconcile()["status"]!="CONSISTENT":
                    raise ValueError("canonical journal failed readback reconciliation")
                if pilot.audit()["outcomes"]!=0:
                    raise ValueError("future outcomes must be pending at capture")
            finally:
                pilot.close();canonical.close();outcomes.close()
            (root/"snapshot.json").write_text(canonical_json(snap.to_dict()),encoding="utf8")
            (root/"forecast_record.json").write_text(canonical_json(record.to_dict()),encoding="utf8")
            (root/"capture_receipt.json").write_text(canonical_json(receipt["receipt"]),encoding="utf8")
            results.append({"market":market,"secid":record.secid,"status":"FROZEN_REAL_SOURCE_BASELINE",
                "forecast_id":record.forecast_id,"direction":record.direction,
                "decision_t0":record.created_at_t0,"source_observed_at":observed,
                "input_bundle_sha256":digest(bundle),"snapshot_id":record.snapshot_id,
                "source_counts":counts,"discarded_m15_partial_buckets":skipped,
                "forecast_record_sha256":digest(canonical_json(record.to_dict()).encode("utf8")),
                "quality_contract":"DEGRADED_NO_INDEPENDENT_CALENDAR_ATTESTATION"})
        except Exception as exc:
            results.append({"market":market,"status":"FROZEN_FORECAST_REFUSED",
                "error_type":type(exc).__name__,"detail":str(exc)[:300]})
    result={"schema":VERSION,"source_origin_declared_real":True,"independent_provider_attestation":False,"source_manifest_sha256":digest(manifest_bytes),
        "research_holdout_used":False,"production_activated":False,
        "forecasts_frozen":sum(x["status"]=="FROZEN_REAL_SOURCE_BASELINE" for x in results),
        "expected_source_markets":manifest["requested_markets"],
        "source_failures":manifest["failed_markets"],"markets":results,
        "strict_ex_ante_proof":False,
        "model_predictive_quality_proven":False,
        "limitations":["Real provider bytes but no signed external timestamp",
            "Unverified cross-day exchange session calendar",
            "Not a calibrated forecast model",
            "No future horizon outcomes exist yet",
            "Pilot and canonical journals have separate DB transactions"]}
    (output_dir/"forecast_manifest.json").write_text(canonical_json(result),encoding="utf8")
    return result


def canonical_json(value):
    return canonical(value)+"\n"


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir",required=True,type=Path)
    parser.add_argument("--output-dir",required=True,type=Path)
    a=parser.parse_args()
    result=freeze_from_receipts(a.source_dir,a.output_dir)
    print(canonical({"forecasts_frozen":result["forecasts_frozen"],
        "markets":result["markets"],"strict_ex_ante_proof":False}))
    if result["forecasts_frozen"] == 0:
        raise SystemExit(2)


if __name__=="__main__":main()
