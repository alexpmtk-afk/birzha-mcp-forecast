"""Read-only audit and actual Forecast V1 price-only historical replay of IMOEX archive.

Research only. No interpolation, no trading, no forecast journal, no LIVE model
changes, and no historical original first-receipt/PIT certification.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from birzha.application.history_policy import require_historical_intraday_scope
from birzha.application.market_data import MarketDataService
from birzha.domain.market import Candle, CandleSeries, Instrument
from scripts.g2_forecast_historical_replay import replay_existing_forecast

MOSCOW = ZoneInfo("Europe/Moscow")
ARCHIVE_MARKER = "BIRZHA_RESEARCH_INTRADAY_2025_2026_V1"
CUT_OFF_MAX = date(2026, 10, 12)
EXPECTED_COUNTS = {"H1": 4199, "M15": 16073}
EXPECTED_DATES = 457


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source(db: Path, expected_sha: str):
    if not db.is_file() or db.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("expected isolated unmodified DuckDB and original SHA256")
    if _sha(db) != expected_sha:
        raise ValueError("DuckDB differs from original immutable research SHA")
    cx = duckdb.connect(str(db), read_only=True)
    try:
        marker = cx.execute(
            "SELECT value FROM research_intraday_meta WHERE key='archive_id'"
        ).fetchone()
        if marker != (ARCHIVE_MARKER,):
            raise ValueError("not canonical research DuckDB")
        yield cx
    finally:
        cx.close()


def inspect_archive(cx) -> dict:
    """Exhaustive native-row and day-checkpoint consistency, not session certification."""
    identity = cx.execute("""
        SELECT DISTINCT symbol,secid,engine,market,board
        FROM research_intraday_candles
    """).fetchall()
    if len(identity) != 1 or identity[0][:2] != ("IMOEX", "IMOEX"):
        raise ValueError("mixed/unknown market contract in research archive")
    counts = dict(cx.execute("""
        SELECT timeframe,count(*) FROM research_intraday_candles GROUP BY timeframe
    """).fetchall())
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"unexpected archive row counts {counts}")
    perday = cx.execute("""
        SELECT trade_date,
               sum(CASE WHEN timeframe='H1' THEN 1 ELSE 0 END) h1,
               sum(CASE WHEN timeframe='M15' THEN 1 ELSE 0 END) m15
        FROM research_intraday_candles GROUP BY trade_date ORDER BY trade_date
    """).fetchall()
    checkpoints = cx.execute("""
        SELECT trade_date,h1_rows,m15_rows,quality,
               incomplete_m15_buckets,skipped_h1_bars,raw_m1_rows
        FROM research_intraday_captured_days ORDER BY trade_date
    """).fetchall()
    if len(perday) != EXPECTED_DATES or len(checkpoints) != EXPECTED_DATES:
        raise ValueError("missing checkpoint/source rows")
    expected_days = ("2025-01-01", CUT_OFF_MAX.isoformat())
    for bar, ck in zip(perday, checkpoints):
        day,h1,m15=bar
        ck_day,ck_h1,ck_m15,quality,missing,short,raw=ck
        if (day != ck_day or h1 != ck_h1 or m15 != ck_m15 or h1 < 1 or m15 < 1
                or not expected_days[0] <= day <= expected_days[1] or
                quality not in ("CAPTURED_WITH_GAPS","CAPTURED_SOURCE_RESPONSE") or
                any(type(v) is not int or v < 0 for v in (missing,short,raw))):
            raise ValueError(f"research candle/checkpoint day mismatch: {day}")
        if (quality == "CAPTURED_WITH_GAPS") != bool(missing or short):
            raise ValueError(f"capture quality not consistent with reported gaps: {day}")
    anomalies = cx.execute("""
        SELECT count(*) FROM research_intraday_candles
        WHERE symbol!='IMOEX' OR secid!='IMOEX' OR timeframe NOT IN ('H1','M15')
           OR trade_date < '2025-01-01' OR trade_date > '2026-10-12'
           OR open <= 0 OR high < greatest(open,close) OR low > least(open,close)
    """).fetchone()[0]
    if anomalies:
        raise ValueError("malformed market price/identity rows")
    gap_days = sum(c[3] == "CAPTURED_WITH_GAPS" for c in checkpoints)
    missing = sum(c[4] for c in checkpoints)
    shortened = sum(c[5] for c in checkpoints)
    first, last=perday[0][0],perday[-1][0]
    return {
        "schema":"IMOEX_G2_STORED_INTRADAY_QUALITY_AUDIT_V1",
        "market":"IMOEX","identity":identity[0],"first_captured":first,
        "last_captured":last,"captured_days":len(perday),
        "H1":counts["H1"],"M15":counts["M15"],
        "days_with_incomplete_source_intervals":gap_days,
        "incomplete_m15_source_bucket_reports":missing,
        "shortened_native_h1_reports":shortened,
        "day_checkpoints_consistent":True,
        "source_candles_valid":True,
        "exchange_session_completeness_certified":False,
        "original_point_in_time_receipt_certified":False,
        "full_live_v1_m15_policy_parity_certified":False,
        "classification":"RESEARCH_PRICE_INPUTS_VALID_WITH_KNOWN_SOURCE_GAPS",
        "release_allowed":False,
    }


def exact_series(cx, instrument: Instrument, tf: str, until: str) -> CandleSeries:
    require_historical_intraday_scope(tf, "2025-01-01", until)
    rows = cx.execute("""
        SELECT begin,end_time,open,high,low,close,volume,value,engine,market,board
        FROM research_intraday_candles
        WHERE symbol='IMOEX' AND secid='IMOEX' AND timeframe=?
          AND trade_date BETWEEN '2025-01-01' AND ?
        ORDER BY begin
    """,[tf,until]).fetchall()
    if not rows:
        raise ValueError("no historical TF bars")
    candles=[]
    for row in rows:
        begin,end,o,h,l,c,v,val,eng,mkt,board=row
        if (eng,mkt,board)!=(instrument.engine,instrument.market,instrument.board):
            raise ValueError("provider contract differs from stored exact IMOEX board/market")
        candles.append(Candle(
            open=o,high=h,low=l,close=c,volume=v,value=val,begin=begin,end=end,
            completed=True, available_at=end, available_at_confidence="INFERRED",
            source="IMMUTABLE_RESEARCH_DUCKDB_RECONSTRUCTED",
        ))
    return CandleSeries(instrument,tf,tuple(candles),source="RESEARCH_DUCKDB")


def frozen_replay(cx, source: MarketDataService, cutoff_day: str, db_sha256: str):
    require_historical_intraday_scope("H1","2025-01-01",cutoff_day)
    decision = date.fromisoformat(cutoff_day)
    if decision < date(2025,9,1) or decision > CUT_OFF_MAX:
        raise ValueError("test decision must be past warmup and within archive")
    instrument=source.resolve("IMOEX",as_of=decision)
    if (instrument.symbol,instrument.secid)!=( "IMOEX","IMOEX"):
        raise ValueError("resolved market is not exact IMOEX")
    # Crucially only source D1 ending AT decision day, no subsequent prices.
    d1=source.candles_for_instrument(
        instrument,timeframe="D1",from_date="2025-01-01",
        till_date=cutoff_day,completed_only=True,
    )
    if not any(x.begin[:10]==cutoff_day for x in d1.candles):
        raise ValueError("requested decision day has no D1 source price")
    intraday={tf:exact_series(cx,instrument,tf,cutoff_day) for tf in ("H1","M15")}
    series={"D1":d1,**intraday}
    cutoff=cutoff_day+"T23:59:59+03:00"
    report=replay_existing_forecast(
        instrument,cutoff_at=cutoff,series=series,source_sha256=db_sha256,
    )
    report["source_archive_sha256"]=db_sha256
    report["no_future_D1_requested"]=True
    report["research_data_classification"]="RECONSTRUCTED_PRICE_ONLY_PIT_UNPROVEN"
    # Extras are outside the original signed replay and must not
    # be passed to evaluate_replay_outcomes without re-signing.
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--expected-sha256",required=True)
    p.add_argument("--mode",choices=("audit","replay"),default="audit")
    p.add_argument("--cutoff-day")
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    if args.output.exists() or args.output.is_symlink() or not args.output.parent.is_dir():
        p.error("refusing to overwrite audit/replay output")
    if args.mode=="replay" and not args.cutoff_day:
        p.error("replay requires explicit cutoff day")
    if args.mode=="audit" and args.cutoff_day:
        p.error("cutoff only applies to replay")
    from contextlib import contextmanager
    with contextmanager(_source)(args.archive,args.expected_sha256) as cx:
        proof=inspect_archive(cx)
        if args.mode=="audit":
            result=proof
        else:
            result={
                "archive_quality":proof,
                "replay":frozen_replay(
                    cx,MarketDataService.default(),args.cutoff_day,
                    args.expected_sha256,
                ),
            }
    args.output.write_text(json.dumps(result,sort_keys=True,
                                      indent=2,ensure_ascii=False,
                                      allow_nan=False)+"\n")
    print(json.dumps({"mode":args.mode,
                      "classification":proof["classification"],
                      "captured_days":proof["captured_days"],
                      "replay_status":result.get("replay",{}).get("assessment_status"),
                      "output":str(args.output)},sort_keys=True))


if __name__=="__main__":
    main()
