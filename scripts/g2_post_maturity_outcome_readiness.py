"""Read-only maturity gate for frozen six-market prospective forecast receipts.

The script does NOT modify DuckDB, does NOT create an Outcome and does NOT fetch
network data. Separate future market/calendar evidence is required after T0.
It accepts the actual original frozen artifact ZIP produced by PR #146.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

VERSION = "G2_POST_MATURITY_READINESS_V1"
HORIZONS = (5, 10, 20)


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def stamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return parsed.astimezone(timezone.utc)


def digest(data):
    return sha256(data).hexdigest()


def _archive(path):
    path = Path(path)
    if not path.exists():
        raise ValueError("frozen forecast archive is missing")
    return ZipFile(path, "r")


def verify_frozen(archive):
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError("duplicate zip entries")
    for name in names:
        p = PurePosixPath(name)
        if name.startswith("/") or ".." in p.parts:
            raise ValueError("unsafe zip path")
    manifest_name = "g2-frozen-forecasts/forecast_manifest.json"
    source_manifest_name = "g2-raw-sources/manifest.json"
    manifest_bytes = archive.read(manifest_name)
    manifest = json.loads(manifest_bytes)
    source_manifest_bytes = archive.read(source_manifest_name)
    source = json.loads(source_manifest_bytes)
    if manifest["source_manifest_sha256"] != digest(source_manifest_bytes):
        raise ValueError("source manifest hash changed")
    if manifest["source_failures"] or source["failed_markets"]:
        raise ValueError("unaccepted source markets")
    if manifest["forecasts_frozen"] != 6 or len(manifest["markets"]) != 6:
        raise ValueError("expected exactly six frozen market records")
    if set(manifest["expected_source_markets"]) != {"SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"}:
        raise ValueError("unexpected six-market manifest")
    source_files = source["all_payload_files"]
    if len(source_files) != 30:
        raise ValueError("expected 30 original MOEX response pages")
    for f in source_files:
        p=PurePosixPath(f["name"])
        if p.name!=f["name"]:
            raise ValueError("raw filename is not a safe basename")
        if digest(archive.read("g2-raw-sources/"+f["name"]))!=f["sha256"]:
            raise ValueError("raw provider source hash mismatch: "+f["name"])
    output=[]
    seen=set()
    for row in manifest["markets"]:
        market = row["market"]
        if market in seen or market not in manifest["expected_source_markets"]:
            raise ValueError("duplicate/unknown market")
        seen.add(market)
        if row["status"] != "FROZEN_REAL_SOURCE_BASELINE":
            raise ValueError("market not frozen")
        rec_bytes = archive.read(f"g2-frozen-forecasts/{market}/forecast_record.json")
        rec = json.loads(rec_bytes)
        receipt = json.loads(archive.read(f"g2-frozen-forecasts/{market}/capture_receipt.json"))
        # The manifest anchors on-disk JSON bytes (with terminal newline),
        # whereas the immutable receipt anchors canonical record JSON.
        # They are two independent, intentionally different hashes.
        if digest(rec_bytes) != row["forecast_record_sha256"]:
            raise ValueError("forecast file-byte hash mismatch")
        if receipt["forecast_sha256"] != digest(canonical(rec).encode()):
            raise ValueError("receipt/canonical forecast hash mismatch")
        if not (rec["forecast_id"]==row["forecast_id"]==receipt["forecast_id"]):
            raise ValueError("forecast ID mismatch")
        if not (rec["snapshot_id"]==row["snapshot_id"]==receipt["snapshot_id"]):
            raise ValueError("snapshot ID mismatch")
        if rec["secid"]!=row["secid"] or rec["symbol"]!=market:
            raise ValueError("contract/market mismatch")
        if rec["reference_price"] is None or float(rec["reference_price"])<=0:
            raise ValueError("invalid frozen reference price")
        if sorted(h["sessions"] for h in rec["horizons"]) != list(HORIZONS):
            raise ValueError("unexpected forecast horizons")
        t0=stamp(rec["created_at_t0"])
        if rec["created_at_t0"]!=row["decision_t0"]:
            raise ValueError("decision T0 mismatch")
        if stamp(receipt["captured_at"])<t0 or stamp(receipt["source_observed_at"])>t0:
            raise ValueError("noncausal receipt/source chronology")
        if stamp(receipt["latest_completed_event_end"])>t0:
            raise ValueError("bar end after decision")
        if receipt["source_sha256"]!=row["input_bundle_sha256"]:
            raise ValueError("original input bundle hash mismatch")
        output.append({"market":market,"secid":rec["secid"],"forecast_id":rec["forecast_id"],
          "snapshot_id":rec["snapshot_id"],"t0":rec["created_at_t0"],
          "direction":rec["direction"],"reference_price":rec["reference_price"],
          "horizons":rec["horizons"],"receipt_sha256":receipt["forecast_sha256"],
          "source_sha256":receipt["source_sha256"]})
    return sorted(output, key=lambda x:x["market"])


def future_d1_candidates(future_archive, market, secid, origin):
    """Inspect only future provider original bytes; never claim a verified calendar."""
    source = json.loads(future_archive.read("g2-raw-sources/manifest.json"))
    instruments = [x for x in source.get("instruments", []) if x["market"]==market]
    if len(instruments)!=1 or instruments[0]["resolved_secid"]!=secid:
        return [], "FUTURE_EXACT_CONTRACT_NOT_AVAILABLE"
    tfs=instruments[0]["timeframes"]
    if "D1" not in tfs:
        return [], "FUTURE_D1_NOT_AVAILABLE"
    out={}
    for page in tfs["D1"]["pages"]:
        filename=page["path"]
        raw=future_archive.read("g2-raw-sources/"+filename)
        if digest(raw)!=page["sha256"]:
            raise ValueError("changed future original bytes")
        received=stamp(page["observed_end_utc"])
        if received<=origin:
            continue
        data=json.loads(raw)
        table=data.get("candles") or {}
        cols=table.get("columns") or []
        if any(k not in cols for k in ("end","close")):
            raise ValueError("future D1 missing complete candle fields")
        for raw_row in table.get("data") or []:
            candle=dict(zip(cols,raw_row))
            # MOEX timestamps without offset use Europe/Moscow, never UTC.
            from zoneinfo import ZoneInfo
            end=datetime.fromisoformat(candle["end"].replace(" ","T"))
            if end.tzinfo is None:
                end=end.replace(tzinfo=ZoneInfo("Europe/Moscow"))
            end=end.astimezone(timezone.utc)
            if end<=origin or end>received:
                continue
            close=float(candle["close"])
            if not 0<close<float("inf"):
                raise ValueError("bad future close")
            day=end.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
            if day in out and out[day]["close"]!=close:
                raise ValueError("conflicting future D1 revision: "+day)
            out[day]={"session":day,"end":end.isoformat(),"close":close,
              "observed_at":page["observed_end_utc"],"raw_sha256":page["sha256"]}
    return [out[k] for k in sorted(out)], None


def readiness(frozen_zip, *, future_zip=None):
    with _archive(frozen_zip) as archive:
        fixed=verify_frozen(archive)
        result=[]
        for record in fixed:
            future=[]
            future_issue="NO_FUTURE_SOURCE_RECEIPT"
            if future_zip is not None:
                with _archive(future_zip) as later:
                    future, future_issue=future_d1_candidates(
                        later,record["market"],record["secid"],stamp(record["t0"]))
            for horizon in HORIZONS:
                status="PENDING_FUTURE_SESSIONS"
                if future_issue:
                    status=future_issue if future_zip is not None else status
                elif len(future)>=horizon:
                    # Counts are NOT automatically equivalent to authenticated
                    # consecutive exchange sessions. Do not append Outcome.
                    status="PENDING_VERIFIED_SESSION_CALENDAR"
                result.append({"market":record["market"],"secid":record["secid"],
                    "forecast_id":record["forecast_id"],"origin_t0":record["t0"],
                    "horizon_sessions":horizon,"forecast_direction":next(
                       x["direction"] for x in record["horizons"] if x["sessions"]==horizon),
                    "available_future_d1_candles":len(future),"status":status,
                    "append_to_canonical_journal":False,
                    "reason":"No trusted exact-contract independent future calendar and completed horizon" })
    return {"schema":VERSION,"source_frozen_zip_sha256":digest(Path(frozen_zip).read_bytes()),
      "actual_future_zip_provided":future_zip is not None,
      "future_zip_sha256":digest(Path(future_zip).read_bytes()) if future_zip else None,
      "forecast_count":len(fixed),"horizon_count":len(result),
      "status_counts":dict(sorted(Counter(x["status"] for x in result).items())),
      "no_lookahead":True,"canonical_outcomes_appended":0,
      "quality_metric_available":False,"outcomes":result,
      "next_gate":"Independently verify exact exchange-session calendar and future observed raw D1; use staging-only append with original frozen receipt after genuine maturity."}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--frozen-zip",required=True,type=Path)
    p.add_argument("--future-zip",type=Path)
    p.add_argument("--output",required=True,type=Path)
    args=p.parse_args(argv)
    if args.output.exists():
        raise ValueError("refusing overwrite of maturity report")
    outcome=readiness(args.frozen_zip,future_zip=args.future_zip)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(canonical(outcome)+"\n",encoding="utf8")
    print(canonical({"forecast_count":outcome["forecast_count"],
     "horizons":outcome["horizon_count"],"statuses":outcome["status_counts"],
     "canonical_outcomes_appended":0}))


if __name__=="__main__":
    main()
