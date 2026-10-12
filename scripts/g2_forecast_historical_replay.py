"""Offline reconstructed replay of the existing price Forecast Engine.

Uses the original snapshot builder and forecast function without fitting or
journal issuance. Missing D1/H1/M15 inputs refuse assessment rather than becoming
zero trend signals. No network provider or journal is constructed.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

from birzha.application.forecast import build_forecast_from_snapshot, ENGINE_VERSION
from birzha.application.snapshot import MarketSnapshotService
from birzha.domain.market import CandleSeries, Instrument

SCHEMA = "G2_FORECAST_HISTORICAL_PRICE_REPLAY_V1"
TIMEFRAMES = ("D1", "H1", "M15")
MOSCOW = ZoneInfo("Europe/Moscow")


def _when(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("invalid candle timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW)
    return parsed.astimezone(timezone.utc)


@dataclass
class _OfflinePrices:
    instrument: Instrument
    series: dict[str, CandleSeries]
    cutoff: datetime

    def resolve(self, symbol: str, *, as_of=None):
        if symbol not in (self.instrument.symbol, self.instrument.secid):
            raise ValueError("instrument identity mismatch")
        return self.instrument

    def candles_for_instrument(self, instrument, *, timeframe, from_date, till_date, completed_only=True, **kwargs):
        if instrument != self.instrument or timeframe not in TIMEFRAMES:
            raise ValueError("unexpected price request")
        selected = []
        for candle in self.series[timeframe].candles:
            begin, end = _when(candle.begin), _when(candle.end)
            if not candle.completed or end > self.cutoff:
                continue
            local_day = begin.astimezone(MOSCOW).date().isoformat()
            if from_date <= local_day <= till_date:
                selected.append(candle)
        return CandleSeries(instrument, timeframe, tuple(selected), source="HISTORICAL_ARCHIVE_RECONSTRUCTED")


def replay_existing_forecast(
    instrument: Instrument, *, cutoff_at: str,
    series: dict[str, CandleSeries], source_sha256: str,
) -> dict:
    if not isinstance(source_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None:
        raise ValueError("original archive SHA-256 required")
    cutoff = _when(cutoff_at)
    if not "2021-01-01" <= cutoff.astimezone(MOSCOW).date().isoformat() <= "2022-12-31":
        raise ValueError("only exposed 2021-22 Development is allowed")
    if set(series) != set(TIMEFRAMES):
        raise ValueError("all three timeframe series must be declared, including missing ones")
    if not isinstance(instrument, Instrument) or not instrument.symbol or not instrument.secid:
        raise ValueError("exact instrument required")
    if instrument.symbol not in {"SBER", "Si", "BR", "GOLD", "IMOEX", "RTSI"}:
        raise ValueError("unsupported project market")
    for tf in TIMEFRAMES:
        item = series[tf]
        if not isinstance(item, CandleSeries) or item.instrument != instrument or item.timeframe != tf:
            raise ValueError("timeframe or exact SECID mismatch")
        previous = None
        for candle in item.candles:
            begin, end = _when(candle.begin), _when(candle.end)
            if end <= begin or (previous is not None and begin <= previous):
                raise ValueError("duplicate or unsorted candle chronology")
            previous = begin
            if type(candle.completed) is not bool:
                raise ValueError("completion must be explicitly boolean")
            if not candle.completed or end > cutoff:
                continue
            if tf in ("H1", "M15"):
                expected = 3600 if tf == "H1" else 900
                if (end - begin).total_seconds() not in (expected - 1, expected):
                    raise ValueError("incomplete intraday bar duration")
            prices = (candle.open, candle.high, candle.low, candle.close)
            if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in prices):
                raise ValueError("finite positive OHLC required")
            for amount in (candle.volume, candle.value):
                if amount is not None and (type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0):
                    raise ValueError("invalid traded amount")
            if not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high:
                raise ValueError("inconsistent OHLC")
    provider = _OfflinePrices(instrument, series, cutoff)
    # Use the same retained windows as the current canonical builder.
    from datetime import timedelta
    day = cutoff.astimezone(MOSCOW).date()
    lookbacks = {"D1":260, "H1":60, "M15":20}
    counts = {tf:provider.candles_for_instrument(
        instrument, timeframe=tf,
        from_date=(day-timedelta(days=lookbacks[tf])).isoformat(),
        till_date=day.isoformat()).count for tf in TIMEFRAMES}
    blockers = [f"{tf}: need 50 completed bars, found {counts[tf]}" for tf in TIMEFRAMES if counts[tf] < 50]
    input_payload = {tf: provider.candles_for_instrument(
        instrument, timeframe=tf, from_date=(day-timedelta(days=lookbacks[tf])).isoformat(),
        till_date=day.isoformat()).to_dict() for tf in TIMEFRAMES}
    input_sha = hashlib.sha256(json.dumps(input_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    report = {
        "schema": SCHEMA, "scope": "RECONSTRUCTED_HISTORICAL_PRICE_ENGINE_ONLY",
        "engine_version": ENGINE_VERSION, "market": instrument.symbol, "secid": instrument.secid,
        "requested_cutoff_at": cutoff_at, "source_sha256": source_sha256,
        "price_input_sha256": input_sha,
        "input_counts": counts, "blockers": blockers,
        "historical_first_receipt": "NOT_PROVEN",
        "strict_historical_as_known_at_T0": False,
        "paid_flow_used": False, "operational_forecast_issued": False,
        "journal_written": False, "release_allowed": False,
        "assessment_status": "MISSING_INPUTS" if blockers else "ENGINE_REPLAYED",
        "research_engine_result": None,
    }
    if blockers:
        return report
    snapshot = MarketSnapshotService(provider, flow=None).build(
        instrument.symbol, as_of_date=day.isoformat())
    if any(state.candles < 50 for state in (snapshot.d1, snapshot.h1, snapshot.m15)):
        raise ValueError("canonical builder did not retain sufficient context")
    if _when(snapshot.as_of) > cutoff:
        raise ValueError("canonical snapshot is later than requested cutoff")
    forecast = build_forecast_from_snapshot(snapshot)
    # This nested research result is NOT issued or appended as a past Forecast Record.
    report["research_engine_result"] = {
        "engine_payload": forecast.to_dict(), "snapshot_id": forecast.snapshot_id,
        "input_scope": "existing default price path; paid flow not requested",
        "snapshot_warnings": list(snapshot.warnings),
    }
    return report


def evaluate_replay_outcomes(report: dict, *, future: CandleSeries,
                             expected_session_dates: tuple[str, ...]) -> dict:
    """Descriptive 5/10/20 D1 results of this research replay, never LIVE Outcomes."""
    if report.get("schema") != SCHEMA or report.get("assessment_status") != "ENGINE_REPLAYED":
        raise ValueError("an admitted original-engine replay is required")
    if report.get("release_allowed") is not False or report.get("journal_written") is not False:
        raise ValueError("research scope required")
    payload = report["research_engine_result"]["engine_payload"]
    if future.timeframe != "D1" or future.instrument.secid != report["secid"]:
        raise ValueError("exact-contract D1 future required")
    if len(expected_session_dates) != 20 or tuple(sorted(set(expected_session_dates))) != expected_session_dates:
        raise ValueError("declare exactly twenty distinct ordered sessions")
    origin = _when(payload["created_at_t0"]).astimezone(MOSCOW).date().isoformat()
    if any(not isinstance(day, str) or date.fromisoformat(day).isoformat() != day for day in expected_session_dates):
        raise ValueError("canonical ISO session dates required")
    if any(not origin < day <= "2022-12-31" or day < "2021-01-01" for day in expected_session_dates):
        raise ValueError("future must stay after origin and inside Development")
    if len(future.candles) > 20:
        raise ValueError("only the declared twenty-session future is assessed")
    dates = []
    for candle in future.candles:
        begin, end = _when(candle.begin), _when(candle.end)
        day = begin.astimezone(MOSCOW).date().isoformat()
        if type(candle.completed) is not bool or not candle.completed or not 0 < (end-begin).total_seconds() <= 86400:
            raise ValueError("only completed future bars are allowed")
        values = (candle.open, candle.high, candle.low, candle.close)
        if any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in values):
            raise ValueError("invalid future OHLC")
        if not candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high:
            raise ValueError("inconsistent future OHLC")
        dates.append(day)
    if tuple(dates) != expected_session_dates[:len(dates)]:
        raise ValueError("missing, duplicate, reordered or wrong future session")
    reference = payload["reference_price"]
    horizons = {item["sessions"]:item for item in payload["horizons"]}
    if set(horizons) != {5,10,20} or type(reference) not in (int,float) or reference <= 0:
        raise ValueError("invalid original-engine horizon or reference")
    results = []
    for h in (5,10,20):
        row = {"horizon_sessions":h, "status":"PENDING", "actual_return_pct":None,
               "direction_hit":None, "always_up_correct":None,
               "absolute_move_error_percentage_points":None,
               "flat_price_error_percentage_points":None}
        if len(future.candles) >= h:
            realized = round((future.candles[h-1].close/reference-1)*100,8)
            direction = horizons[h]["direction"]
            if direction not in ("UP","DOWN","NEUTRAL"):
                raise ValueError("invalid original direction")
            move = horizons[h]["expected_move_pct"]
            if move is not None and (type(move) not in (int,float) or not math.isfinite(move)):
                raise ValueError("invalid original numeric forecast")
            hit = realized > 0 if direction == "UP" else realized < 0 if direction == "DOWN" else None
            row.update(status="OBSERVED", actual_return_pct=realized, direction_hit=hit,
                always_up_correct=realized > 0 if direction != "NEUTRAL" else None,
                absolute_move_error_percentage_points=round(abs(move-realized),8) if move is not None else None,
                flat_price_error_percentage_points=round(abs(realized),8) if move is not None else None)
        results.append(row)
    return {"schema":"G2_FORECAST_HISTORICAL_PRICE_OUTCOMES_V1",
            "scope":report["scope"], "engine_version":report["engine_version"],
            "price_input_sha256":report["price_input_sha256"],
            "outcomes":results, "historical_first_receipt":"NOT_PROVEN",
            "independent_exchange_calendar_verified":False,
            "journal_written":False,"release_allowed":False}
