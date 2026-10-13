"""Read-only two-year IMOEX expected-session audit; never downloads H1/M1 or writes a DB.

Compare MOEX historical activity dates against completed native D1 candle dates.
Disagreement is reported as NOT_VERIFIED, not silently replaced by a guessed
weekday list. H1/M15 full-session certification remains a separate task.
"""
from __future__ import annotations

import argparse
from datetime import date
import json

from birzha.application.history_policy import (
    INTRADAY_RESEARCH_FIRST_DATE,
    INTRADAY_RESEARCH_LAST_DATE,
    latest_safe_d1_calendar_date,
    require_historical_intraday_scope,
)
from birzha.application.market_data import MarketDataService
from birzha.providers.moex_calendar import MoexTradingCalendar


def audit(market_data: MarketDataService, from_date: str, till_date: str) -> dict:
    """Pure read-only source check using existing rate-governed provider."""
    require_historical_intraday_scope("H1", from_date, till_date)
    instrument = market_data.resolve("IMOEX", as_of=date.fromisoformat(till_date))
    if instrument.secid.upper() != "IMOEX" or instrument.asset_class != "index":
        raise ValueError("IMOEX expected fixed exact index contract")
    session_calendar = MoexTradingCalendar(market_data.provider)
    activity_days = session_calendar.dates(
        engine=instrument.engine, market=instrument.market,
        board=instrument.board, security=instrument.secid,
        from_date=date.fromisoformat(from_date), till_date=date.fromisoformat(till_date),
    )
    daily = market_data.candles_for_instrument(
        instrument, timeframe="D1", from_date=from_date,
        till_date=till_date, completed_only=True,
    )
    candle_days = sorted({
        date.fromisoformat(c.begin[:10]).isoformat()
        for c in daily.candles if c.completed and from_date <= c.begin[:10] <= till_date
    })
    activity_set = {day.isoformat() for day in activity_days}
    candle_set = set(candle_days)
    missing_from_price = sorted(activity_set - candle_set)
    missing_from_activity = sorted(candle_set - activity_set)
    return {
        "scope": "IMOEX_SOURCE_CALENDAR_READ_ONLY",
        "symbol": "IMOEX", "secid": instrument.secid,
        "from_date": from_date, "till_date": till_date,
        "history_activity_sessions": len(activity_days),
        "native_completed_d1_sessions": len(candle_days),
        "activity_without_d1_count": len(missing_from_price),
        "d1_without_activity_count": len(missing_from_activity),
        "activity_without_d1_sample": missing_from_price[:15],
        "d1_without_activity_sample": missing_from_activity[:15],
        "first_observed_d1": candle_days[0] if candle_days else None,
        "last_observed_d1": candle_days[-1] if candle_days else None,
        "independent_calendar_proven": False,
        "state": (
            "SOURCE_CALENDAR_CONSISTENT_NOT_PIT_VERIFIED"
            if activity_days and candle_days and not missing_from_price and not missing_from_activity
            else "CALENDAR_NOT_VERIFIED"
        ),
        "no_intraday_prices_downloaded": True,
        "no_db_write": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("plan", "audit"), default="plan")
    parser.add_argument("--from-date", default=INTRADAY_RESEARCH_FIRST_DATE.isoformat())
    parser.add_argument("--till-date", default=None)
    args = parser.parse_args(argv)
    safe_today = min(INTRADAY_RESEARCH_LAST_DATE.isoformat(), latest_safe_d1_calendar_date())
    till = args.till_date or safe_today
    try:
        if not (date.fromisoformat(args.from_date).isoformat() == args.from_date
                and date.fromisoformat(till).isoformat() == till):
            raise ValueError("canonical ISO dates required")
        if args.from_date > till:
            raise ValueError("from_date later than till_date")
        require_historical_intraday_scope("H1", args.from_date, till)
        if till > safe_today:
            raise ValueError("only safely published past sessions eligible")
    except ValueError as exc:
        parser.error(str(exc))
    if args.mode == "plan":
        print(json.dumps({
            "state": "PLAN_NO_NETWORK_NO_WRITE",
            "market": "IMOEX", "from_date": args.from_date,
            "till_date": till, "calendar_audit_pending": True,
        }, sort_keys=True))
        return 0
    report = audit(MarketDataService.default(), args.from_date, till)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["state"] == "SOURCE_CALENDAR_CONSISTENT_NOT_PIT_VERIFIED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
