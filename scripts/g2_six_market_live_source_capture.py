"""One-shot governed live-source receipt inventory for six MOEX market roots.

No ForecastRecord or trading action is created here. The point is to retain raw
response bytes and first observed-at timestamps for exact-resolved securities.
Public MOEX requests MUST pass through the existing MoexIssClient._request
governor. Input is always captured from the network, never historical replay.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo
import argparse

MARKETS = ("SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI")
# At least 50 completed M15 bars require >1 SBER trading session.
# Keep five-day calendar window bounded by MAX_PAGES; do not fabricate bars.
TIMEFRAMES = {"D1": (24, 115), "H1": (60, 18), "M1": (1, 5)}
COLUMNS = "open,close,high,low,value,volume,begin,end"
RECEIPT_VERSION = "G2_SIX_MARKET_LIVE_SOURCE_RECEIPT_V1"
MOEX_TZ = ZoneInfo("Europe/Moscow")
MAX_PAGES = 8


def stable(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def clock_now():
    return datetime.now(timezone.utc)


def timestamp(dt):
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timezone-aware receipt clock required")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def market_time(dt):
    value = datetime.fromisoformat(str(dt).replace(" ", "T"))
    return value.replace(tzinfo=MOEX_TZ) if value.tzinfo is None else value.astimezone(MOEX_TZ)


def _source_request(provider, path, params, clock):
    started = clock()
    timestamp(started)
    response = provider._request(path, params)  # Mandatory existing ISS governor.
    ended = clock()
    timestamp(ended)
    if ended < started or (ended - started).total_seconds() > 90:
        raise ValueError("noncausal or excessively long request clock interval")
    if response.status_code != 200 or not isinstance(response.body, bytes) or not response.body:
        raise ValueError("no successful governed source body")
    header_date = response.headers.get("Date") or response.headers.get("date")
    if header_date:
        header_date = timestamp(parsedate_to_datetime(header_date))
    return response, timestamp(started), timestamp(ended), header_date


def _parse_page(response, *, earliest, latest, observed):
    payload = json.loads(response.body.decode("utf8"))
    table = payload.get("candles") or {}
    columns = table.get("columns") or []
    for key in ("open", "close", "high", "low", "begin", "end"):
        if key not in columns:
            raise ValueError("MOEX response missing required candle fields: " + key)
    raw_rows = table.get("data") or []
    if not isinstance(raw_rows, list) or len(raw_rows) > 500:
        raise ValueError("invalid candle page or oversize response")
    result = []
    for item in raw_rows:
        row = dict(zip(columns, item))
        begin, end = market_time(row["begin"]), market_time(row["end"])
        # Some MOEX D1 bars include the previous evening session.
        # Validate completed exchange-local candle END, not UTC begin date.
        if not earliest <= end.date() <= latest or begin.date() < earliest - timedelta(days=2):
            raise ValueError(
                "MOEX row outside pinned exchange-local window: "
                f"begin={row['begin']!r}, end={row['end']!r}, "
                f"start={earliest}, cutoff={latest}"
            )
        if not begin < end <= observed:
            raise ValueError("forming or unobserved candle included")
        prices = [float(row[k]) for k in ("open", "close", "high", "low")]
        if any(not math.isfinite(x) or x <= 0 for x in prices):
            raise ValueError("invalid or missing positive OHLC")
        if prices[2] < max(prices[0], prices[1]) or prices[3] > min(prices[0], prices[1]):
            raise ValueError("inconsistent candle high/low")
        result.append({"begin": begin.astimezone(timezone.utc).isoformat(),
                       "end": end.astimezone(timezone.utc).isoformat(),
                       "open": prices[0], "close": prices[1], "high": prices[2],
                       "low": prices[3], "volume": row.get("volume"), "value": row.get("value")})
    cursor = payload.get("candles.cursor") or {}
    if cursor.get("data"):
        cursor_columns = cursor.get("columns") or []
        cursor_row = dict(zip(cursor_columns, cursor["data"][0]))
        total = int(cursor_row.get("TOTAL") or cursor_row.get("total") or len(raw_rows))
        page_size = int(cursor_row.get("PAGESIZE") or cursor_row.get("pagesize") or max(1,len(raw_rows)))
    else:
        # Official ISS may omit its cursor and cap each response at 500.
        # Keep total unknown; caller MUST page until a short/empty page,
        # reject repeats, and never certify after MAX_PAGES full pages.
        total, page_size = None, 500
    if (total is not None and total < len(raw_rows)) or page_size > 500 or page_size < 1:
        raise ValueError("inconsistent source pagination")
    return result, total, page_size


def collect_one(provider, instrument, timeframe, now, clock, folder):
    interval, days = TIMEFRAMES[timeframe]
    cutoff = now.astimezone(MOEX_TZ).date() - timedelta(days=1)
    since = cutoff - timedelta(days=days)
    path = (f"/engines/{instrument.engine}/markets/{instrument.market}/boards/"
            f"{instrument.board}/securities/{instrument.secid}/candles.json")
    params = {"iss.meta":"off", "iss.only":"candles,candles.cursor",
              "from": since.isoformat(), "till":cutoff.isoformat(),
              "interval":interval, "candles.columns": COLUMNS}
    pages = []
    candles = []
    expected_total = None
    page_size = None
    for index in range(MAX_PAGES):
        start = 0 if page_size is None else page_size * index
        response, started, ended, header_date = _source_request(provider,path,{**params,"start":start},clock)
        # Original HTTP response bytes MUST survive even an invalid page.
        file_name=f"{instrument.symbol}_{instrument.secid}_{timeframe}_{index}.json"
        with (folder/file_name).open("xb") as handle:
            handle.write(response.body)
        page_rows, total, size = _parse_page(response,
            earliest=since,latest=cutoff,observed=datetime.fromisoformat(ended.replace("Z","+00:00")))
        if page_size is None:
            expected_total,page_size=total,size
        elif total != expected_total or size != page_size:
            raise ValueError("source pagination changed during capture; fail closed")
        if not page_rows and expected_total is not None and start < expected_total:
            raise ValueError("unexpected empty paginated page")
        pages.append({"path":file_name, "start":start,"sha256":sha256(response.body).hexdigest(),
                     "bytes":len(response.body),"observed_start_utc":started,
                     "observed_end_utc":ended,"http_date_utc":header_date,
                     "row_count":len(page_rows)})
        candles.extend(page_rows)
        if expected_total is None:
            if len(page_rows) < page_size:
                break
        elif len(candles) >= expected_total:
            break
    else:
        raise ValueError("maximum governed source pages exceeded")
    if expected_total is not None and len(candles) != expected_total:
        raise ValueError("incomplete pagination or source count changed")
    ends = [v["end"] for v in candles]
    if ends != sorted(set(ends)):
        raise ValueError("duplicate or unsorted response sessions")
    if not candles:
        raise ValueError("no completed source candles in requested window")
    return {"timeframe":timeframe,"interval":interval,"source_market":instrument.market,
        "exchange_board":instrument.board,"exact_secid":instrument.secid,
        "requested_from":since.isoformat(),"requested_till":cutoff.isoformat(),
        "total_completed":len(candles),"first_candle_begin_utc":candles[0]["begin"],
        "last_candle_end_utc":candles[-1]["end"],"pages":pages,
        "final_observed_end_utc":pages[-1]["observed_end_utc"]}


def capture_all(provider, market_data, out_dir, *, clock=clock_now, markets=MARKETS):
    """Bounded fail-closed six-market inventory. No secrets or HOME access."""
    out_dir=Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("must use a fresh empty output directory")
    started=clock()
    timestamp(started)
    out_dir.mkdir(parents=True,exist_ok=True)
    instruments=[]
    failed=[]
    for market in markets:
        try:
            # Explicit exact resolution. Rolling futures roots must NOT be
            # passed to a candles endpoint as if they were exact SECIDs.
            instrument=market_data.resolve(market)
            if instrument.secid.upper()==market.upper() and market.upper() in {"SI","BR","GOLD"}:
                raise ValueError("unresolved futures root treated as a SECID")
            if not instrument.engine or not instrument.market or not instrument.board:
                raise ValueError("missing exact instrument routing")
            instrument_receipts={}
            for timeframe in TIMEFRAMES:
                instrument_receipts[timeframe]=collect_one(
                    provider,instrument,timeframe,started,clock,out_dir)
            instruments.append({"market":market,"resolved_secid":instrument.secid,
                "instrument":instrument.to_dict(),"timeframes":instrument_receipts,
                "status":"SOURCE_CAPTURE_COMPLETE"})
        except Exception as exc:
            # Keep original bytes of partial captures as evidence but refuse
            # to certify this instrument's completeness.
            failed.append({"market":market,"error_type":type(exc).__name__,
                           "detail":str(exc)[:300],"status":"SOURCE_CAPTURE_INCOMPLETE"})
    files=sorted(p for p in out_dir.glob("*.json") if p.name!="manifest.json")
    total_pages=sum(len(v["timeframes"][tf]["pages"]) for v in instruments for tf in TIMEFRAMES)
    manifest={"schema":RECEIPT_VERSION,"origin":"REAL_MOEX_PUBLIC_ISS_CAPTURE",
        "is_forecast":False,"is_outcome":False,"strict_external_timestamp_attested":False,
        "capture_started_utc":timestamp(started),"capture_completed_utc":timestamp(clock()),
        "requested_markets":list(markets),"complete_markets":[i["market"] for i in instruments],
        "failed_markets":failed,"instruments":instruments,
        "verified_complete_market_count":len(instruments),"complete_pages":total_pages,
        "all_payload_files":[{"name":p.name,"sha256":sha256(p.read_bytes()).hexdigest()} for p in files],
        "limitations":["Raw snapshots not forecasts", "Provider/clock not externally signed",
                       "No immutable forecast record yet", "No research holdout used"]}
    (out_dir/"manifest.json").write_text(stable(manifest)+"\n",encoding="utf8")
    return manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir",type=Path,required=True)
    args=ap.parse_args()
    from birzha.application.market_data import MarketDataService
    from birzha.application.upstream_control import ProcessUpstreamControlPlane
    from birzha.providers.moex_iss import MoexIssClient
    from birzha.providers.moex_resolver import MoexDirectInstrumentResolver
    control=ProcessUpstreamControlPlane()
    provider=MoexIssClient(control_plane=control)
    market_data=MarketDataService(provider=provider,
                  direct_resolver=MoexDirectInstrumentResolver(provider))
    result=capture_all(provider,market_data,args.output_dir)
    print(stable({"schema":RECEIPT_VERSION,"complete_market_count":result["verified_complete_market_count"],
                  "failures":result["failed_markets"],"pages":result["complete_pages"],
                  "capture_started_utc":result["capture_started_utc"]}))
    if result["verified_complete_market_count"] != len(MARKETS):
        raise SystemExit(2)


if __name__=="__main__":
    main()
