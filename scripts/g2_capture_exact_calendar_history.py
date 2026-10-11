"""Governed, opt-in exact SECID /history receipt capture under G2 market scope.

Original response bytes remain in memory until the ONE immutable DuckDB
transaction; no separate unaudited writable history DB or silent fallback.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json

from birzha.application.g2_calendar_witness import SCHEMA
from scripts.g2_six_market_live_source_capture import _source_request


MAX_HISTORY_PAGES = 20
MAX_HISTORY_ROWS_PER_PAGE = 100


def capture_exact_history(
    provider, instrument: dict, *, from_day: str, through_day: str,
    clock=lambda: datetime.now(timezone.utc),
) -> dict:
    try:
        ins = {k: instrument[k] for k in ("engine", "market", "board", "secid")}
        if any(not isinstance(v, str) or not v or "/" in v or "\\" in v for v in ins.values()):
            raise ValueError("unsafe exact instrument path")
        start_day, end_day = date.fromisoformat(from_day), date.fromisoformat(through_day)
        if start_day > end_day or (end_day - start_day).days > 120:
            raise ValueError("history requested beyond bounded source range")
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError("invalid original exact SECID history route") from exc
    path = (f"/history/engines/{ins['engine']}/markets/{ins['market']}/"
            f"boards/{ins['board']}/securities/{ins['secid']}.json")
    params = {
        "iss.meta": "off", "iss.only": "history,history.cursor",
        "history.columns": "TRADEDATE,NUMTRADES,VOLUME,VALUE",
        "from": from_day, "till": through_day,
    }
    offset = 0
    total_expected = None
    page_size_expected = None
    output = []
    for _ in range(MAX_HISTORY_PAGES):
        response, started, ended, http_date = _source_request(
            provider, path, {**params, "start": offset}, clock
        )
        if len(response.body) > 2_000_000:
            raise ValueError("oversized original history page")
        source = json.loads(response.body)
        table = source.get("history") if isinstance(source, dict) else None
        if not isinstance(table, dict):
            raise ValueError("missing original MOEX /history rows")
        columns = table.get("columns")
        rows = table.get("data")
        if (not isinstance(columns, list) or not isinstance(rows, list)
            or not 0 < len(rows) <= MAX_HISTORY_ROWS_PER_PAGE
            or any(key not in columns for key in
                   ("TRADEDATE", "NUMTRADES", "VOLUME", "VALUE"))
            or any(not isinstance(row, list) or len(row) != len(columns)
                   for row in rows)):
            raise ValueError("invalid original /history page")
        cursor = source.get("history.cursor")
        if not isinstance(cursor, dict) or not cursor.get("data"):
            raise ValueError("uncertified /history pagination cursor")
        ccols, crows = cursor.get("columns"), cursor.get("data")
        if not isinstance(ccols, list) or not isinstance(crows, list):
            raise ValueError("bad /history cursor")
        summary = dict(zip(ccols, crows[0]))
        total, size = int(summary["TOTAL"]), int(summary["PAGESIZE"])
        if total < len(rows) or size < 1 or size > MAX_HISTORY_ROWS_PER_PAGE:
            raise ValueError("invalid /history cursor values")
        if total_expected is None:
            total_expected, page_size_expected = total, size
        elif (total_expected, page_size_expected) != (total, size):
            raise ValueError("history pagination shifted during capture")
        output.append({
            "start": offset, "raw_body": response.body,
            "sha256": sha256(response.body).hexdigest(),
            "observed_start_utc": started, "observed_end_utc": ended,
            "http_date_utc": http_date,
        })
        offset += len(rows)
        if offset >= total:
            if offset != total:
                raise ValueError("overfilled /history cursor")
            break
        if len(rows) != size:
            raise ValueError("short nonterminal /history page")
    else:
        raise ValueError("bounded /history page limit exhausted")
    return {
        "schema": SCHEMA, **ins, "path": path,
        "requested_from": from_day, "requested_till": through_day,
        "pages": output,
    }
