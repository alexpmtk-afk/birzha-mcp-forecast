"""Expose the existing exact-instrument stored series across session boundaries.

No resampling, gap removal, price adjustment, source write or network request.
This is diagnostic reading, not point-in-time forecast admission.
"""
from __future__ import annotations
from datetime import date, datetime
from zoneinfo import ZoneInfo
from birzha.application.historical_data import HistoricalDataService
from birzha.application.market_data import is_futures_root_symbol

VERSION = "STORED_SESSION_SERIES_V1"
MAX_DAYS = 367
MAX_ROWS = 50000

class StoredSessionSeriesError(ValueError):
    pass


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Moscow"))
    return parsed.astimezone(ZoneInfo("UTC"))


def stored_session_series(history: HistoricalDataService, *, secid: str,
                          timeframe: str, from_date: str, till_date: str) -> dict:
    """Reuse load_exact/store.read, preserving every source bar and real gap."""
    if not isinstance(secid, str) or secid != secid.strip() or not 1 <= len(secid) <= 64:
        raise StoredSessionSeriesError("INVALID_EXACT_SECID")
    if is_futures_root_symbol(secid):
        raise StoredSessionSeriesError("EXACT_CONTRACT_REQUIRED")
    if timeframe not in {"D1", "H1", "M15"}:
        raise StoredSessionSeriesError("UNSUPPORTED_TIMEFRAME")
    try:
        start, finish = date.fromisoformat(from_date), date.fromisoformat(till_date)
    except (TypeError, ValueError):
        raise StoredSessionSeriesError("INVALID_DATE_RANGE") from None
    if start.isoformat() != from_date or finish.isoformat() != till_date:
        raise StoredSessionSeriesError("INVALID_DATE_RANGE")
    if not 0 <= (finish-start).days < MAX_DAYS:
        raise StoredSessionSeriesError("DATE_RANGE_EXCEEDS_BOUND")
    instrument = history.store.stored_instrument(secid)
    if instrument is None:
        raise StoredSessionSeriesError("STORED_INSTRUMENT_UNAVAILABLE")
    if instrument.secid != secid:
        raise StoredSessionSeriesError("INSTRUMENT_MISMATCH")
    series = history.load_exact(instrument, timeframe=timeframe,
                                from_date=from_date, till_date=till_date)
    if series.instrument.secid != secid or series.timeframe != timeframe:
        raise StoredSessionSeriesError("SERIES_IDENTITY_MISMATCH")
    if len(series.candles) > MAX_ROWS:
        raise StoredSessionSeriesError("ROW_BOUND_EXCEEDED")
    previous_end = None
    for candle in series.candles:
        try:
            begin, end = _instant(candle.begin), _instant(candle.end)
        except (TypeError, ValueError, AttributeError):
            raise StoredSessionSeriesError("INVALID_STORED_TIME") from None
        if not from_date <= candle.begin[:10] <= till_date:
            raise StoredSessionSeriesError("ROW_OUTSIDE_REQUEST")
        if end < begin or (previous_end is not None and begin <= previous_end):
            raise StoredSessionSeriesError("UNORDERED_DUPLICATE_OR_OVERLAPPING_BARS")
        previous_end = end
    return {
        "status": "STORED_SERIES_READ" if series.candles else "STORED_SERIES_EMPTY",
        "contract_version": VERSION,
        "operation": "EXISTING_LOAD_EXACT_READ",
        "price_adjustment": "NONE",
        "resampling": "NONE",
        "synthetic_bars_added": 0,
        "inter_session_gaps": "PRESERVED",
        "session_calendar_status": "UNVERIFIED",
        "historical_availability_status": "UNVERIFIED",
        "forecast_admission": "NOT_GRANTED",
        "date_filter_basis": "SOURCE_BEGIN_LITERAL_DATE_NOT_TRADE_SESSION_DATE",
        "naive_timestamp_interpretation": "Europe/Moscow; validation only, stored text unchanged",
        "stored_metadata_is_not_historical_attestation": True,
        "from_date": from_date, "till_date": till_date,
        "completed_count": sum(c.completed for c in series.candles),
        "incomplete_count": sum(not c.completed for c in series.candles),
        **series.to_dict(),
    }
