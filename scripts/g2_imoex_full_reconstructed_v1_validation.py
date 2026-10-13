"""Full-source reconstructed IMOEX Forecast V1 shadow backtest, 2025-2026.

Read-only. Replays every eligible trading-date cut-off using original V1 engine,
sealed BEFORE computing descriptive 5/10/20-session outcomes. Source dates
follow reconstructed MOEX D1 candles, NOT an independently certified calendar.
No prices from 2023-24 OOS. No actual historical first-receipt attestation.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from birzha.application.market_data import MarketDataService
from birzha.domain.market import CandleSeries
from scripts.g2_forecast_historical_replay import (
    replay_existing_forecast, evaluate_replay_outcomes,
)
from scripts.g2_imoex_saved_archive_replay import _sha, _source, exact_series, inspect_archive, CUT_OFF_MAX

MOSCOW=ZoneInfo("Europe/Moscow")
DB_SHA="caa235288d442cf88fd9f8ae38a5c51071042badb6b9295386495468aaa2d4c6"
DECISION_FIRST="2025-04-01"
CLOSEST_ALLOWED="2026-10-12"


def replay_all(db: Path, *, d1_provider, expected_hash: str = DB_SHA) -> dict:
    """Freeze all T0 reports before any future-price evaluation, then assess."""
    from contextlib import contextmanager
    with contextmanager(_source)(db,expected_hash) as cx:
        proof=inspect_archive(cx)
        instrument=d1_provider.resolve("IMOEX",as_of=CUT_OFF_MAX)
        if (instrument.symbol,instrument.secid)!=( "IMOEX","IMOEX"):
            raise ValueError("source changed instrument")
        actual=d1_provider.candles_for_instrument(
            instrument, timeframe="D1",from_date="2025-01-01",
            till_date=CLOSEST_ALLOWED,completed_only=True,
        )
        if actual.timeframe!="D1" or actual.instrument!=instrument:
            raise ValueError("D1 exact instrument mismatch")
        ds=tuple(actual.candles)
        if any(not c.completed for c in ds):
            raise ValueError("unfinished source D1")
        days=[c.begin[:10] for c in ds]
        if (days!=sorted(set(days)) or len(days)<100
                or min(days)<"2025-01-01" or max(days)>CLOSEST_ALLOWED):
            raise ValueError("invalid D1 series time scope/dedup")
        intraday={tf:exact_series(cx,instrument,tf,CLOSEST_ALLOWED)
                  for tf in ("H1","M15")}
        h1=intraday["H1"].candles
        m15=intraday["M15"].candles
        frozen=[]
        blockers=[]
        for pos,cutoff in enumerate(days):
            if cutoff<DECISION_FIRST:
                continue
            # Price-only snapshot cannot ever see a future candle; these
            # copies contain only candles whose BEGIN is <= decision date.
            d1past=CandleSeries(instrument,"D1",ds[:pos+1],source="MOEX_D1_RECONSTRUCTED")
            historical={"D1":d1past}
            for tf,rows in (("H1",h1),("M15",m15)):
                before=tuple(c for c in rows if c.begin[:10]<=cutoff)
                historical[tf]=CandleSeries(instrument,tf,before,source="RESEARCH_DUCKDB")
            replay=replay_existing_forecast(
                instrument,cutoff_at=cutoff+"T23:59:59+03:00",
                series=historical,source_sha256=expected_hash,
            )
            if replay["assessment_status"]=="ENGINE_REPLAYED":
                frozen.append((pos,cutoff,replay))
            elif replay["assessment_status"]=="MISSING_INPUTS":
                blockers.append({"day":cutoff,"blockers":replay["blockers"]})
            else:
                raise ValueError("unexpected V1 replay status")
        # Freeze complete: only NOW may the separate outcome function be
        # passed any subsequent D1. Never use outcome labels to modify V1.
        forecasted=[]
        horizon_observations={h:[] for h in (5,10,20)}
        for pos,cutoff,report in frozen:
            days_forward=days[pos+1:pos+21]
            row={
                "cutoff_date":cutoff,"assessment_status":"ENGINE_REPLAYED",
                "replay_sha256":report["replay_sha256"],
                "price_input_sha256":report["price_input_sha256"],
                "snapshot_id":report["research_engine_result"]["snapshot_id"],
                "H1_context_bars":report["input_counts"]["H1"],
                "M15_context_bars":report["input_counts"]["M15"],
                "D1_context_bars":report["input_counts"]["D1"],
                "evaluation_status":"NOT_YET_20_D1_CANDIDATES",
            }
            if len(days_forward)==20:
                future=CandleSeries(instrument,"D1",ds[pos+1:pos+21],
                                    source="MOEX_D1_OUTCOMES_RECONSTRUCTED")
                outcomes=evaluate_replay_outcomes(
                    report,future=future,
                    expected_session_dates=tuple(days_forward),
                )
                row["evaluation_status"]="DESCRIPTIVE_RECONSTRUCTED"
                row["outcomes"]=outcomes["outcomes"]
                for outcome in outcomes["outcomes"]:
                    if outcome["status"]=="OBSERVED":
                        horizon_observations[outcome["horizon_sessions"]].append(outcome)
            forecasted.append(row)
        summary={}
        for h,observations in horizon_observations.items():
            directional=[o for o in observations if o["direction_hit"] is not None]
            avg=lambda values: sum(values)/len(values) if values else None
            summary[str(h)]={
                "outcomes_observed":len(observations),
                "directional_non_neutral":len(directional),
                "direction_hit_rate":avg([1 if o["direction_hit"] else 0 for o in directional]),
                "always_up_same_sample_rate":avg([1 if o["always_up_correct"] else 0 for o in directional]),
                "avg_absolute_move_error_percentage_points":avg([
                    o["absolute_move_error_percentage_points"] for o in observations
                    if o["absolute_move_error_percentage_points"] is not None]),
                "avg_flat_price_error_percentage_points":avg([
                    o["flat_price_error_percentage_points"] for o in observations
                    if o["flat_price_error_percentage_points"] is not None]),
            }
        report={
            "schema":"G2_IMOEX_RECONSTRUCTED_V1_FULL_RESEARCH_V1",
            "market":"IMOEX","period":"2025-01-01..2026-10-12",
            "archive_sha256":expected_hash,
            "d1_source_count":len(ds),"d1_source_sha256":hashlib.sha256(
                json.dumps(actual.to_dict(),sort_keys=True,separators=(",",":"),
                           allow_nan=False).encode()).hexdigest(),
            "archive_quality":proof,
            "decisions_considered":len(frozen)+len(blockers),
            "engine_replays_completed":len(frozen),
            "missing_context_decisions":len(blockers),
            "cutoffs_with_20_future_candidate_D1":sum(
                row["evaluation_status"]=="DESCRIPTIVE_RECONSTRUCTED"
                for row in forecasted
            ),
            "horizon_summary":summary,
            "blocked_cutoffs":blockers,
            "forecast_rows":forecasted,
            "source_calendar":"MOEX_RECONSTRUCTED_D1_NOT_INDEPENDENT_SESSION_CERTIFICATION",
            "historical_first_receipt":"NOT_PROVEN",
            "strict_replay_as_known_at_T0":False,
            "eligible_for_trading":False,
            "release_allowed":False,
            "model_optimized_on_result":False,
            "model_parameters_unchanged":True,
            "future_prices_delivered_to_engine":False,
            "live_forecast_journal_changed":False,
        }
        return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--expected-sha256",default=DB_SHA)
    args=p.parse_args(argv)
    if args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir():
        p.error("new output file in existing folder required")
    full=replay_all(args.archive,d1_provider=MarketDataService.default(),
                    expected_hash=args.expected_sha256)
    args.output.write_text(json.dumps(full,ensure_ascii=False,sort_keys=True,
                                      allow_nan=False,indent=2)+"\n")
    print(json.dumps({
        "status":"RESEARCH_SHADOW_ONLY",
        "replays":full["engine_replays_completed"],
        "horizons":full["horizon_summary"],
        "blockers":full["missing_context_decisions"],
        "source_first_receipt":"NOT_PROVEN",
        "release_allowed":False,
    },ensure_ascii=False,sort_keys=True))


if __name__=="__main__":
    main()
