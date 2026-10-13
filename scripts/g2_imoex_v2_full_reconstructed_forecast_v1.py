"""Recompute entire reconstructed IMOEX Forecast V1 research using source-compatible H1/M15.

No new engine version, training, LIVE journal, trade, or ex-ante first-receipt
claim. Freeze every historical Forecast V1 output before outcome assessment.
"""
from __future__ import annotations

import argparse
from datetime import date,datetime,timedelta
import hashlib
import json
import math
from pathlib import Path

from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_source_compat import IMOEXResearchPriceView
from birzha.application.snapshot import MarketSnapshotService
from scripts.g2_imoex_source_interval_recovery import sha256
from scripts.g2_imoex_v2_research_engine_smoke import ArchiveResearchMarketData

EXPECTED_V2_SHA="e66262c6d809e7a81b2b91d6e1068e60b3dac114b0310c862d05127c36b29a5c"


def all_reconstructed(source,view):
    last=date(2026,10,12)
    instrument=source.resolve("IMOEX",as_of=last)
    if (instrument.symbol,instrument.secid)!=("IMOEX","IMOEX"):
        raise ValueError("unexpected security")
    d1=source.candles_for_instrument(instrument,timeframe="D1",
                                    from_date="2025-01-01",
                                    till_date=last.isoformat(),completed_only=True)
    days=[c.begin[:10] for c in d1.candles]
    if (len(days)<440 or days!=sorted(set(days)) or
        days[0]<"2025-01-01" or days[-1]>last.isoformat()):
        raise ValueError("missing or out of order D1 source candles")
    # STAGE 1. Construct ALL historic snapshots and freeze predictions,
    # before any future D1 row can reach outcome computation.
    frozen=[]
    for index,day in enumerate(days):
        if day<"2025-04-01":
            continue
        cutoff=datetime.fromisoformat(day+"T23:59:59+03:00")
        offline=ArchiveResearchMarketData(
            instrument,d1,view,source_compatible=True,cutoff_at=cutoff)
        snapshot=MarketSnapshotService(offline,flow=None).build(
            "IMOEX",as_of_date=day)
        if min(snapshot.d1.candles,snapshot.h1.candles,snapshot.m15.candles)<50:
            raise ValueError("historical decision has insufficient D1/H1/M15")
        forecast=build_forecast_from_snapshot(snapshot)
        payload=forecast.to_dict()
        if payload.get("secid")!="IMOEX" or not payload.get("horizons"):
            raise ValueError("unexpected Forecast V1 payload")
        seal=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),
                                      allow_nan=False).encode()).hexdigest()
        frozen.append({
            "d1_position":index,"day":day,
            "engine_version":forecast.engine_version,
            "snapshot_id":forecast.snapshot_id,
            "inputs":{"D1":snapshot.d1.candles,"H1":snapshot.h1.candles,
                      "M15":snapshot.m15.candles},
            "price_engine_payload_sha256":seal,
            "forecast_engine_payload":payload,
        })
    results={str(h):[] for h in (5,10,20)}
    case_rows=[]
    # STAGE 2. Outcome assessment reads actual future D1 only AFTER
    # predictions are frozen. It must never re-enter the original engine.
    for item in frozen:
        day=item["day"]
        pos=item["d1_position"]
        forecast=item["forecast_engine_payload"]
        row={k:v for k,v in item.items() if k!="forecast_engine_payload"}
        future=d1.candles[pos+1:pos+21]
        row["outcomes"]={}
        for h in (5,10,20):
            if len(future)<h:
                row["outcomes"][str(h)]={"status":"PENDING"}
                continue
            reference=forecast["reference_price"]
            if type(reference) not in (int,float) or not math.isfinite(reference) or reference<=0:
                raise ValueError("invalid historical forecast reference price")
            realized=(future[h-1].close/reference-1)*100
            horizon=next((x for x in forecast["horizons"] if x["sessions"]==h),None)
            if horizon is None or horizon["direction"] not in ("UP","DOWN","NEUTRAL"):
                raise ValueError("unexpected original V1 horizon")
            direction=horizon["direction"]
            move=horizon["expected_move_pct"]
            if move is not None and (type(move) not in (int,float) or not math.isfinite(move)):
                raise ValueError("nonfinite historic expected move")
            observed={
                "status":"OBSERVED","actual_return_pct":realized,
                "direction":direction,
                "direction_hit":(realized>0 if direction=="UP" else
                                 realized<0 if direction=="DOWN" else None),
                "always_up_correct":(realized>0 if direction!="NEUTRAL" else None),
                "absolute_move_error_pp":abs(move-realized) if move is not None else None,
                "flat_price_error_pp":abs(realized) if move is not None else None,
                "actual_future_day":future[h-1].begin[:10],
            }
            row["outcomes"][str(h)]=observed
            results[str(h)].append(observed)
        case_rows.append(row)
    summary={}
    for h,rows in results.items():
        direction=[r for r in rows if r["direction_hit"] is not None]
        def avg(v):
            return sum(v)/len(v) if v else None
        summary[h]={
            "observed":len(rows),"non_neutral":len(direction),
            "direction_hit_rate":avg([int(v["direction_hit"]) for v in direction]),
            "always_up_same_sample_rate":avg([int(v["always_up_correct"]) for v in direction]),
            "absolute_move_mae_pp":avg([
                v["absolute_move_error_pp"] for v in rows
                if v["absolute_move_error_pp"] is not None]),
            "flat_price_mae_pp":avg([
                v["flat_price_error_pp"] for v in rows
                if v["flat_price_error_pp"] is not None]),
        }
    return {
        "schema":"IMOEX_V1_FULL_RECONSTRUCTED_SOURCE_COMPAT_OUTCOMES_V2",
        "period":"2025-01-01..2026-10-12",
        "native_short_source_H1":view.quality_counts()["H1"]["NATIVE_H1_SHORT_OBSERVED"],
        "partial_source_M15":view.quality_counts()["M15"]["SOURCE_M15_WITH_FEWER_THAN_15_M1"],
        "D1_source_dates":len(days),
        "forecast_V1_replays":len(frozen),
        "horizon_summary":summary,
        "rows":case_rows,
        "predictions_frozen_before_outcome_evaluation":True,
        "future_prices_passed_to_forecast_engine":False,
        "engine_parameters_changed":False,
        "point_in_time_first_receipt_verified":False,
        "exchange_session_completeness_verified":False,
        "quality_flag":"RECONSTRUCTED_HISTORICAL_RESEARCH_PIT_UNPROVEN",
        "no_trade":True,"release_allowed":False,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--expected-sha256",default=EXPECTED_V2_SHA)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    if (args.output.exists() or args.output.is_symlink() or
        not args.output.parent.is_dir()):
        p.error("new research report destination required")
    if sha256(args.archive)!=args.expected_sha256:
        p.error("V2 source-compatible research DuckDB SHA does not match")
    view=IMOEXResearchPriceView(args.archive)
    try:
        result=all_reconstructed(MarketDataService.default(),view)
    finally:
        view.close()
    result["source_compatible_archive_sha256"]=args.expected_sha256
    args.output.write_text(json.dumps(result,sort_keys=True,indent=2,
                                      allow_nan=False,ensure_ascii=False)+"\n")
    print(json.dumps({
        "status":result["quality_flag"],
        "forecast_V1_replays":result["forecast_V1_replays"],
        "horizons":result["horizon_summary"],
        "release_allowed":result["release_allowed"],
    },sort_keys=True))


if __name__=="__main__":
    main()
