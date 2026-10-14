"""RTSI-only MOEX ISS observed candles: D1/native H1/M1->quality-labelled M15.

Standalone GitHub-hosted research capture, never touching HOME, IMOEX, or Sheets.
Missing M1 is not interpolated; incomplete quarters are retained as research only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from birzha.application.history_policy import latest_safe_d1_calendar_date
from birzha.application.market_data import MarketDataService
from birzha.application.research_intraday_archive import _moment

MOSCOW = ZoneInfo("Europe/Moscow")
FIELDS = ("record_key", "calendar_date", "trade_session_date", "session_id",
          "bar_start_time", "bar_end_time", "available_at", "available_at_confidence",
          "contract_code", "open", "high", "low", "close", "volume",
          "number_of_trades", "turnover", "is_complete", "source_interval",
          "source_candles", "source_id", "research_status", "normalized_sha256",
          "source_archive_sha256", "capture_day_status", "source_kind", "caveat")
FIRST_NEW_D1 = "2026-09-16"
CAVEAT = "RECONSTRUCTED_SOURCE; CALENDAR_NOT_INDEPENDENTLY_ATTESTED; PIT_UNKNOWN"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iso(value: str) -> str:
    return _moment(value).isoformat()


def validate(bar) -> None:
    values = (bar.open, bar.high, bar.low, bar.close)
    if not all(type(x) in (int, float) and math.isfinite(x) and x > 0 for x in values):
        raise ValueError("invalid native OHLC")
    if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
        raise ValueError("inconsistent native OHLC")
    for x in (bar.volume, bar.value):
        if x is not None and (type(x) not in (int, float) or not math.isfinite(x) or x < 0):
            raise ValueError("invalid nonprice field")
    if bar.completed is not True or _moment(bar.end) < _moment(bar.begin):
        raise ValueError("uncompleted/native reversed interval")


def row(tf: str, bar, *, count: int = 1, complete: bool = True, origin: str = "MOEX_NATIVE") -> dict:
    validate(bar)
    start, end = iso(bar.begin), iso(bar.end)
    values = {
        "record_key": f"RTSI|{tf}|RTSI|{start}",
        "calendar_date": start[:10], "trade_session_date": None, "session_id": None,
        "bar_start_time": start, "bar_end_time": end, "available_at": None,
        "available_at_confidence": "UNKNOWN", "contract_code": "RTSI",
        "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
        # RTSI is a calculated index: provider's zero is not index turnover/trades.
        "volume": None, "number_of_trades": None, "turnover": None,
        "is_complete": complete, "source_interval": "M1" if tf == "M15" else tf,
        "source_candles": count, "source_id": "MOEX_ISS",
        "research_status": "SOURCE_COMPLETE_NOT_PIT_ATTESTED" if complete else "SOURCE_PARTIAL_NOT_MODEL_ELIGIBLE",
        "source_archive_sha256": None, "capture_day_status": "SOURCE_OBSERVED_CALENDAR_UNVERIFIED",
        "source_kind": origin, "caveat": CAVEAT,
    }
    payload = {field: values[field] for field in FIELDS[:20]}
    values["normalized_sha256"] = hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False).encode("utf-8")).hexdigest()
    return values


def build_m15(m1: tuple) -> list[dict]:
    bins = defaultdict(list)
    seen = set()
    for bar in m1:
        validate(bar)
        start, end = _moment(bar.begin), _moment(bar.end)
        if start.second or start.microsecond or (end - start).total_seconds() not in (59, 60):
            raise ValueError("invalid minute source period")
        key = start.isoformat()
        if key in seen:
            raise ValueError("duplicate native source minute")
        seen.add(key)
        bins[start.replace(minute=(start.minute // 15) * 15)].append((start, bar))
    out = []
    for start, items in sorted(bins.items()):
        items.sort(key=lambda x: x[0])
        expected = [start + timedelta(minutes=i) for i in range(15)]
        complete = len(items) == 15 and [x[0] for x in items] == expected
        first, last = items[0][1], items[-1][1]
        # Local ad-hoc candle object with source OHLC only, never inferred prices.
        from birzha.domain.market import Candle
        bar = Candle(open=first.open, high=max(x[1].high for x in items),
                     low=min(x[1].low for x in items), close=last.close,
                     volume=None, value=None, begin=start.isoformat(),
                     end=(start + timedelta(minutes=15) - timedelta(seconds=1)).isoformat(),
                     completed=True)
        out.append(row("M15", bar, count=len(items), complete=complete,
                       origin="MOEX_M1_AGGREGATED_STRICT" if complete else "MOEX_M1_OBSERVED_PARTIAL"))
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        for item in rows:
            fh.write(json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                separators=(",", ":")) + "\n")


def capture_day(service, instrument, day: str):
    data = {}
    for tf in ("D1", "H1", "M1"):
        series = service.candles_for_instrument(
            instrument, timeframe=tf, from_date=day, till_date=day, completed_only=True)
        if series.instrument.secid != "RTSI":
            raise ValueError("source SECID drift")
        data[tf] = tuple(x for x in series.candles if _moment(x.begin).date().isoformat() == day)
    if not data["D1"] or not data["H1"] or not data["M1"]:
        raise ValueError(f"missing D1/H1/M1 observed source {day}: {[len(data[t]) for t in data]}")
    h1, raw_m1 = [], []
    for c in data["H1"]:
        duration = (_moment(c.end) - _moment(c.begin)).total_seconds()
        start = _moment(c.begin)
        if duration <= 0 or duration > 3600:
            raise ValueError("invalid hourly source duration")
        complete = duration in (3599, 3600) and start.minute == start.second == 0
        h1.append(row("H1", c, complete=complete, origin="MOEX_NATIVE_H1"))
    for c in data["M1"]:
        validate(c)
        raw_m1.append({"secid": "RTSI", "timeframe": "M1", "begin": iso(c.begin),
                       "end": iso(c.end), "open": c.open, "high": c.high,
                       "low": c.low, "close": c.close, "source_volume": c.volume,
                       "source_value": c.value, "completed": c.completed,
                       "observed_at": c.observed_at})
    return ([row("D1", c, origin="MOEX_NATIVE_D1") for c in data["D1"]],
            h1, build_m15(data["M1"]), raw_m1)


def main():
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--day")
    mode.add_argument("--month")
    p.add_argument("--till-date", default="2026-10-13")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists() or a.output.is_symlink() or not a.output.parent.is_dir():
        p.error("output must be a new directory in an existing parent")
    upper = min(date.fromisoformat(a.till_date), date.fromisoformat(latest_safe_d1_calendar_date()))
    if upper > date(2026, 10, 13):
        upper = date(2026, 10, 13)
    if a.day:
        days = [date.fromisoformat(a.day)]
        if days[0] > upper:
            p.error("trial day not closed")
    else:
        first = date.fromisoformat(a.month + "-01")
        if first.year not in (2025, 2026) or first > upper:
            p.error("month outside permitted research range")
        end = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        end = min(end, upper)
        days = None
    a.output.mkdir()
    service = MarketDataService.default()
    instrument = service.resolve("RTSI", as_of=days[0] if days else end)
    if (instrument.secid, instrument.symbol, instrument.engine, instrument.market,
        instrument.board, instrument.asset_class) != ("RTSI", "RTSI", "stock", "index", "RTSI", "index"):
        raise ValueError("RTSI index identity failure")
    if days is None:
        daily = service.candles_for_instrument(
            instrument, timeframe="D1", from_date=first.isoformat(),
            till_date=end.isoformat(), completed_only=True)
        days = sorted({date.fromisoformat(_moment(c.begin).date().isoformat()) for c in daily.candles
                       if first <= _moment(c.begin).date() <= end})
        if not days:
            raise ValueError("month has no MOEX D1 candidate days")
    all_rows = {"D1": [], "H1": [], "M15": [], "M1": []}
    quality, errors = [], []
    for day in days:
        try:
            d1, h1, m15, raw_m1 = capture_day(service, instrument, day.isoformat())
            if a.month:
                d1 = [c for c in d1 if c["calendar_date"] >= FIRST_NEW_D1]
            all_rows["D1"].extend(d1)
            all_rows["H1"].extend(h1)
            all_rows["M15"].extend(m15)
            all_rows["M1"].extend(raw_m1)
            q = {"date": day.isoformat(), "D1": len(d1), "H1": len(h1),
                 "H1_complete": sum(x["is_complete"] for x in h1),
                 "H1_short": sum(not x["is_complete"] for x in h1),
                 "M1": len(raw_m1), "M15": len(m15),
                 "M15_complete": sum(x["is_complete"] for x in m15),
                 "M15_partial": sum(not x["is_complete"] for x in m15)}
            quality.append(q)
            print(json.dumps(q, sort_keys=True), flush=True)
        except Exception as exc:
            errors.append({"day": day.isoformat(), "error_type": type(exc).__name__,
                           "error": str(exc)[:250]})
            print(json.dumps(errors[-1], sort_keys=True), flush=True)
    for tf, items in all_rows.items():
        filename = ("source_M1.jsonl" if tf == "M1" else f"candles_{tf}.jsonl")
        if len({x["begin"] if tf == "M1" else x["record_key"] for x in items}) != len(items):
            raise ValueError("cross-day duplicate source key")
        write_jsonl(a.output / filename, items)
    report = {"schema": "RTSI_MOEX_RESEARCH_CAPTURE_V1", "instrument": instrument.to_dict(),
              "scope": a.day or a.month, "upper_bound": upper.isoformat(),
              "capture_type": "pilot" if a.day else "monthly",
              "origin": "OFFICIAL_MOEX_ISS", "algorithm": "RTSI_STRICT_15_DISTINCT_M1_V1",
              "independent_calendar_verified": False, "historical_first_receipt_verified": False,
              "exchange_session_coverage_verified": False, "source_m1_persisted": True,
              "source_candles": {k: len(v) for k, v in all_rows.items()},
              "days": quality, "errors": errors}
    (a.output / "quality.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    sha = {f.name: digest(f) for f in a.output.iterdir() if f.is_file()}
    (a.output / "manifest.json").write_text(json.dumps(
        {"schema": "RTSI_MOEX_RESEARCH_MANIFEST_V1", "source_files_sha256": sha,
         "rows": report["source_candles"], "errors_count": len(errors), "scope": report["scope"]},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (a.output / "SHA256SUMS.txt").write_text(
        "".join(f"{digest(f)}  {f.name}\n" for f in sorted(a.output.iterdir()) if f.is_file()),
        encoding="ascii")
    if errors or not all_rows["H1"] or not all_rows["M15"] or not all_rows["M1"]:
        raise RuntimeError("source capture incomplete; audit files retained")
    print("RTSI_REAL_PRICE_CAPTURE_PASS " + json.dumps(report["source_candles"], sort_keys=True))


if __name__ == "__main__":
    main()
