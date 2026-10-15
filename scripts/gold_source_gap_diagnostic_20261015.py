"""Research-only exact-contract source gap audit. No store writes or synthetic bars."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from birzha.application.market_data import MarketDataService
from birzha.providers.moex_calendar import MoexTradingCalendar

TZ = ZoneInfo("Europe/Moscow")
SPEC = (("2025-06-09", "GDM5"), ("2026-03-02", "GDH6"))
FIELDS = ("timeframe", "secid", "begin", "end", "open", "high", "low", "close", "volume", "value")


def moment(text):
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return (dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ))


def valid(candle, tf):
    try:
        b, e = moment(candle.begin), moment(candle.end)
        duration = (e-b).total_seconds()
        expected = {"M1": (59, 60), "M15": (899, 900), "H1": (3599, 3600), "D1": (86399, 86400)}[tf]
        vals = (candle.open, candle.high, candle.low, candle.close)
        if not candle.completed or duration not in expected:
            return False
        if not all(type(n) in (float, int) and math.isfinite(n) and n > 0 for n in vals):
            return False
        if not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high:
            return False
        if candle.volume is not None and (not math.isfinite(candle.volume) or candle.volume < 0):
            return False
        if candle.value is not None and (not math.isfinite(candle.value) or candle.value < 0):
            return False
        return True
    except (TypeError, ValueError, KeyError):
        return False


def row(tf, secid, candle):
    return [tf, secid, candle.begin, candle.end,
            candle.open, candle.high, candle.low, candle.close, candle.volume, candle.value]


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(FIELDS)
        writer.writerows(rows)


def one_day(service, day_text, secid, target):
    day = date.fromisoformat(day_text)
    instrument = service.resolve("GOLD", as_of=day)
    identity = (instrument.symbol, instrument.secid, instrument.engine, instrument.market, instrument.board)
    if identity != ("GOLD", secid, "futures", "forts", "RFUD"):
        raise RuntimeError(f"MOEX exact contract mismatch: {identity}")

    target.mkdir(parents=True, exist_ok=True)
    series = {}
    for timeframe in ("D1", "H1", "M1"):
        series[timeframe] = service.candles_for_instrument(
            instrument, timeframe=timeframe,
            from_date=day_text, till_date=day_text, completed_only=True)

    source = {}
    for tf, src in series.items():
        candles = [c for c in src.candles if moment(c.begin).date() == day]
        valid_rows = [c for c in candles if valid(c, tf)]
        rejected = [c.begin for c in candles if not valid(c, tf)]
        write_csv(target / f"MOEX_NATIVE_{tf}.csv", [row(tf, secid, c) for c in candles])
        source[tf] = {
            "rows_returned": len(candles),
            "valid_rows": len(valid_rows),
            "rejected_begins": rejected,
        }

    observed_m1 = [c for c in series["M1"].candles if moment(c.begin).date() == day]
    bins = defaultdict(list)
    for c in observed_m1:
        t = moment(c.begin)
        bucket = t.replace(minute=(t.minute//15)*15,second=0,microsecond=0)
        bins[bucket].append((t,c))
    incomplete=[]
    completed=[]
    invalid=[]
    for start, pairs in sorted(bins.items()):
        pairs.sort(key=lambda x:x[0])
        expected = [start + timedelta(minutes=n) for n in range(15)]
        observed = [x[0] for x in pairs]
        missing = [x.isoformat() for x in expected if x not in observed]
        repeated = len(observed) != len(set(observed))
        incorrect = [c.begin for _,c in pairs if not valid(c,"M1")]
        if missing or repeated or incorrect or len(pairs)!=15:
            incomplete.append({
                "bucket_begin":start.isoformat(),
                "received_minutes":len(pairs),
                "missing_minute_begins":missing,
                "duplicate_begin":repeated,
                "invalid_candles":incorrect,
                "classification":"SOURCE_INCOMPLETE_REASON_UNVERIFIED",
            })
        else:
            values=[x[1] for x in pairs]
            completed.append({
                "timeframe":"M15","secid":secid,
                "begin":start.isoformat(),"end":(start+timedelta(minutes=15,seconds=-1)).isoformat(),
                "open":values[0].open,"high":max(c.high for c in values),
                "low":min(c.low for c in values),"close":values[-1].close,
                "volume":sum(c.volume for c in values) if all(c.volume is not None for c in values) else None,
                "value":sum(c.value for c in values) if all(c.value is not None for c in values) else None,
            })
    write_csv(target / "STRICT_M15.csv", [[c.get(k) for k in FIELDS] for c in completed])

    try:
        calendar = MoexTradingCalendar(service.provider)
        listed_days = calendar.dates(
            engine=instrument.engine, market=instrument.market, board=instrument.board,
            security=secid, from_date=day, till_date=day)
        day_status = "TRADE_ACTIVITY_DATE_RETURNED" if day in listed_days else "NO_ACTIVITY_DATE_RETURNED"
    except Exception as error:
        day_status = f"CALENDAR_UNAVAILABLE_{type(error).__name__}"

    report = {
        "market":"GOLD", "secid":secid, "day":day_text,
        "engine":instrument.engine, "market_name":instrument.market,
        "board":instrument.board,"MOEX_calendar_day":day_status,
        "source":source,
        "full_m15_count":len(completed),
        "incomplete_m15_count":len(incomplete),
        "incomplete_m15_intervals":incomplete,
        "interpretation":"Missing native M1 is not proof that a trade occurred or a provider row was lost",
        "historical_first_receipt":"NOT_VERIFIED",
        "official_session_open_close":"NOT_ATTESTED",
        "synthetic_bars_added":0,
        "source_write":"NONE",
    }
    (target/"gap_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n", encoding="utf-8")
    checks=[]
    for f in sorted(target.iterdir()):
        if f.is_file():
            raw=f.read_bytes()
            digest=hashlib.sha256(raw).hexdigest()
            assert hashlib.sha256(f.read_bytes()).hexdigest()==digest
            checks.append({"file":f.name,"bytes":len(raw),"sha256":digest})
    (target/"checksums.json").write_text(json.dumps(checks,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:report[k] for k in
        ("market","secid","day","MOEX_calendar_day","full_m15_count","incomplete_m15_count")},
        ensure_ascii=False),flush=True)
    return report


def main():
    service = MarketDataService.default()
    root = Path("research_diagnostics/GOLD")
    errors=[]
    all_reports=[]
    for day, secid in SPEC:
        path=root/day+"_"+secid if False else root/f"{day}_{secid}"
        try:
            all_reports.append(one_day(service,day,secid,path))
        except Exception as e:
            errors.append({"date":day,"secid":secid,"error":f"{type(e).__name__}: {str(e)[:500]}"})
    root.mkdir(parents=True,exist_ok=True)
    summary={"research_only":True,"MOEX_source_invoked":True,
             "reports":len(all_reports),"errors":errors,
             "expected":len(SPEC),"scope":"one-day exact GD contracts; no store access"}
    (root/"summary.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    if errors:
        raise SystemExit(2)
    if any(r["incomplete_m15_count"] for r in all_reports):
        raise SystemExit(1)

if __name__=="__main__":
    main()
