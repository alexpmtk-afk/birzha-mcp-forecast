"""Q-087: pure/offline replay of MOEX ISS M1 source bytes into one M15.

No network, path writes, independent transport authentication or level journal
admission. Operator-supplied HTTP metadata is checked for consistency only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from birzha.domain.market import Candle, Instrument

VERSION = "G2_M1_M15_RAW_REPLAY_V1"
MOSCOW = ZoneInfo("Europe/Moscow")
COLUMNS = ("open", "close", "high", "low", "value", "volume", "begin", "end")
MAX_PAGES = 12
MAX_BYTES = 1_500_000
PAGE_SIZE = 500


class SourceReplayRefused(ValueError):
    """Original raw pages cannot prove the proposed completed M15."""


@dataclass(frozen=True, slots=True)
class OriginalM1Page:
    """Caller-reported response bytes, query URL and request/receipt metadata.

    HTTP host and times are operator-supplied; this object is not TLS or TSA.
    """
    raw_body: bytes
    raw_sha256: str
    request_url: str
    http_status: int
    content_type: str
    request_started_utc: str
    response_received_utc: str
    start: int


def _utc(value: str) -> datetime:
    try:
        if not isinstance(value, str):
            raise ValueError("timestamp must be text")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing timezone")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise SourceReplayRefused("explicit valid UTC-offset receipt required") from exc


def _m1_time(value: str) -> datetime:
    """Native MOEX ISS candle times are exchange-local, without timezone."""
    try:
        if not isinstance(value, str):
            raise ValueError("invalid native candle time")
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None:
            raise ValueError("unexpected timezone in native candle")
        return result.replace(tzinfo=MOSCOW).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise SourceReplayRefused("invalid native exchange-local M1 timestamp") from exc


def _identity(instrument: Instrument) -> str:
    if not isinstance(instrument, Instrument):
        raise TypeError("typed Instrument required")
    fields = (instrument.engine, instrument.market, instrument.board, instrument.secid)
    if any(not isinstance(x, str) or not x or not
           all(ch.isalnum() or ch in "_.-" for ch in x) for x in fields):
        raise SourceReplayRefused("unsafe or ambiguous exact MOEX identity")
    return "/".join(fields)


def _strict_json(data: bytes) -> dict:
    def object_pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SourceReplayRefused("repeated key in original JSON")
            result[key] = value
        return result
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=object_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               SourceReplayRefused("nonfinite source JSON")))
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise SourceReplayRefused("not a valid original JSON response") from exc
    if not isinstance(value, dict):
        raise SourceReplayRefused("top-level JSON is not a response object")
    return value


def _table(payload: dict, name: str) -> tuple[list[str], list]:
    obj = payload.get(name)
    if not isinstance(obj, dict):
        raise SourceReplayRefused("required ISS table missing")
    cols, rows = obj.get("columns"), obj.get("data")
    if (not isinstance(cols, list) or len(cols) != len(set(cols))
        or not all(isinstance(x, str) for x in cols)
        or not isinstance(rows, list)):
        raise SourceReplayRefused("invalid table columns or data")
    return cols, rows


def _number(value, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (
        value > 0 if positive else value >= 0)


def _minute(row: dict) -> dict:
    if any(name not in row for name in COLUMNS):
        raise SourceReplayRefused("source candle misses OHLCV or time field")
    start, end = _m1_time(row["begin"]), _m1_time(row["end"])
    if (start.second != 0 or start.microsecond != 0
        or end != start + timedelta(seconds=59)):
        raise SourceReplayRefused("M1 interval not a full exact minute")
    o, c, h, l = (row[x] for x in ("open", "close", "high", "low"))
    volume, value = row["volume"], row["value"]
    if (not all(_number(x, positive=True) for x in (o, c, h, l))
        or not _number(volume) or not _number(value)
        or h < max(o, c) or l > min(o, c) or l > h):
        raise SourceReplayRefused("invalid original M1 OHLCV")
    return {"begin": start, "end": end, "open": float(o), "close": float(c),
            "high": float(h), "low": float(l),
            "volume": float(volume), "value": float(value)}


def replay_completed_m15(*, instrument: Instrument, pages: tuple[OriginalM1Page, ...],
                         m15_begin_utc: str, evaluated_at_utc: str,
                         candidate: Candle | None = None) -> dict:
    """Rebuild one complete M15 from retained original M1 pages, fail-closed.

    Never fabricates Candle.available_at_confidence=EXACT, independent
    source authentication, first-receipt TSA or live Level Event acceptance.
    """
    identity = _identity(instrument)
    if not isinstance(pages, tuple) or not 1 <= len(pages) <= MAX_PAGES:
        raise SourceReplayRefused("bounded nonempty original page tuple required")
    start = _utc(m15_begin_utc)
    evaluated = _utc(evaluated_at_utc)
    if start.minute % 15 or start.second or start.microsecond:
        raise SourceReplayRefused("unaligned M15 start")
    end = start + timedelta(minutes=15) - timedelta(seconds=1)
    if end > evaluated:
        raise SourceReplayRefused("M15 not yet completed by evaluation")
    expected_offset = 0
    observed_previous = None
    saw_cursor = None
    total = None
    cursor_page_size = None
    seen_params = None
    entries = []
    meta = []
    last_page_count = 0
    for index, p in enumerate(pages):
        if not isinstance(p, OriginalM1Page):
            raise TypeError("typed original HTTP page required")
        if not isinstance(p.raw_body, bytes) or not 0 < len(p.raw_body) <= MAX_BYTES:
            raise SourceReplayRefused("original bytes absent or oversized")
        computed = sha256(p.raw_body).hexdigest()
        if not isinstance(p.raw_sha256, str) or p.raw_sha256 != computed:
            raise SourceReplayRefused("raw page hash mismatch")
        if type(p.start) is not int or p.start != expected_offset:
            raise SourceReplayRefused("pagination offset missing, repeated, or unsorted")
        if type(p.http_status) is not int or p.http_status != 200:
            raise SourceReplayRefused("non-success original HTTP status")
        if not isinstance(p.content_type, str) or p.content_type.split(";")[0].lower().strip() != "application/json":
            raise SourceReplayRefused("non-JSON content type")
        u = urlsplit(p.request_url)
        path = "/iss/engines/" + identity.split("/")[0] + "/markets/" + identity.split("/")[1] + \
               "/boards/" + identity.split("/")[2] + "/securities/" + identity.split("/")[3] + "/candles.json"
        if (u.scheme != "https" or u.hostname != "iss.moex.com" or
            u.port is not None or u.username is not None or
            u.password is not None or u.fragment or u.path != path):
            raise SourceReplayRefused("incorrect original exact-SECID URL claim")
        q = parse_qs(u.query, keep_blank_values=True)
        if any(len(v) != 1 for v in q.values()) or q.get("interval") != ["1"] or q.get("start") != [str(p.start)]:
            raise SourceReplayRefused("invalid M1 route/query or pagination")
        if any(v not in q for v in ("from", "till")):
            raise SourceReplayRefused("source date query missing")
        try:
            lower, upper = date.fromisoformat(q["from"][0]), date.fromisoformat(q["till"][0])
        except (ValueError, TypeError) as exc:
            raise SourceReplayRefused("invalid MOEX date filters") from exc
        if lower > upper or not lower <= start.astimezone(MOSCOW).date() <= upper:
            raise SourceReplayRefused("source query excludes requested M15 date")
        stable = {key: value[0] for key, value in q.items() if key != "start"}
        if seen_params is not None and seen_params != stable:
            raise SourceReplayRefused("source query changed between pages")
        seen_params = stable
        request_started = _utc(p.request_started_utc)
        received = _utc(p.response_received_utc)
        if not request_started <= received <= evaluated:
            raise SourceReplayRefused("future or inverted source receipt")
        if (received-request_started).total_seconds() > 90:
            raise SourceReplayRefused("unbounded request duration")
        if observed_previous is not None and received < observed_previous:
            raise SourceReplayRefused("original pages observed out of order")
        observed_previous = received
        payload = _strict_json(p.raw_body)
        cols, raw_rows = _table(payload, "candles")
        if not set(COLUMNS).issubset(cols) or len(raw_rows) > PAGE_SIZE:
            raise SourceReplayRefused("missing M1 columns or oversized page")
        page_rows = []
        for item in raw_rows:
            if not isinstance(item, list) or len(item) != len(cols):
                raise SourceReplayRefused("malformed original M1 row")
            minute = _minute(dict(zip(cols, item)))
            # Causality applies to every retained M1, not only the 15
            # selected minutes. An unselected future row would otherwise
            # leak into a supposedly historical source commitment.
            if minute["end"] > received:
                raise SourceReplayRefused(
                    "original source page includes M1 not completed by its own receipt")
            page_rows.append(minute)
        cursor = payload.get("candles.cursor")
        cursor_present = cursor is not None
        if saw_cursor is not None and saw_cursor != cursor_present:
            raise SourceReplayRefused("pagination cursor mode changed")
        saw_cursor = cursor_present
        if cursor_present:
            cursor_cols, cursor_rows = _table(payload, "candles.cursor")
            if len(cursor_rows) != 1 or not isinstance(cursor_rows[0], list) or len(cursor_rows[0]) != len(cursor_cols):
                raise SourceReplayRefused("malformed original cursor")
            fields = dict(zip(cursor_cols,cursor_rows[0]))
            if "TOTAL" not in fields or "PAGESIZE" not in fields:
                raise SourceReplayRefused("cursor must include TOTAL/PAGESIZE")
            page_total, page_size = fields["TOTAL"], fields["PAGESIZE"]
            if (type(page_total) is not int or type(page_size) is not int
                or not 1 <= page_size <= PAGE_SIZE or page_total < 1
                or page_total > MAX_PAGES * PAGE_SIZE or
                len(raw_rows) > page_size):
                raise SourceReplayRefused("invalid original cursor values")
            if total is not None and page_total != total:
                raise SourceReplayRefused("pagination TOTAL changed")
            if cursor_page_size is not None and cursor_page_size != page_size:
                raise SourceReplayRefused("pagination PAGESIZE changed")
            if (p.start % page_size != 0 or p.start >= page_total
                or len(raw_rows) != min(page_size, page_total-p.start)):
                raise SourceReplayRefused("cursor page truncated or wrong offset")
            cursor_page_size = page_size
            total = page_total
        elif index < len(pages)-1 and len(raw_rows) != PAGE_SIZE:
            # Without a cursor only a full 500-row source page may
            # legally precede another requested page.
            raise SourceReplayRefused("unexpected nonterminal short source page")
        if not raw_rows and not entries:
            raise SourceReplayRefused("no original M1 rows")
        expected_offset += len(raw_rows)
        last_page_count = len(raw_rows)
        entries.extend(page_rows)
        meta.append({"start":p.start,"row_count":len(raw_rows),
                     "source_sha256":computed,"received_at_utc":received.isoformat(),
                     "original_url_claim":p.request_url})
    if saw_cursor:
        if expected_offset != total:
            raise SourceReplayRefused("truncated source cursor pagination")
    else:
        if last_page_count >= PAGE_SIZE:
            raise SourceReplayRefused("no short/empty terminal page; cannot certify complete pagination")
    if not entries:
        raise SourceReplayRefused("no original M1 source rows")
    begins = [x["begin"] for x in entries]
    if begins != sorted(set(begins)):
        raise SourceReplayRefused("non-monotonic or duplicated M1 source")
    selected = [x for x in entries if start <= x["begin"] < start + timedelta(minutes=15)]
    if len(selected) != 15 or selected[0]["begin"] != start:
        raise SourceReplayRefused("M15 requires exactly 15 original one-minute rows")
    if any(selected[i]["begin"] != start + timedelta(minutes=i) for i in range(15)):
        raise SourceReplayRefused("M15 source has gaps or unexpected minute alignment")
    if any(x["end"] > observed_previous for x in selected):
        raise SourceReplayRefused("future unfinished original M1 source")
    m15 = {
        "begin":start.isoformat().replace("+00:00","Z"),
        "end":end.isoformat().replace("+00:00","Z"),
        "open":selected[0]["open"],"close":selected[-1]["close"],
        "high":max(x["high"] for x in selected),
        "low":min(x["low"] for x in selected),
        "volume":round(sum(x["volume"] for x in selected),10),
        "value":round(sum(x["value"] for x in selected),10),
    }
    if candidate is not None:
        if not isinstance(candidate,Candle):
            raise TypeError("candidate must be typed Candle")
        try:
            same=(candidate.begin == m15["begin"] and candidate.end == m15["end"]
                  and all(type(getattr(candidate,k)) in (float,int)
                          and math.isclose(getattr(candidate,k),m15[k],rel_tol=1e-12,abs_tol=1e-9)
                          for k in ("open","close","high","low","value","volume")))
        except (TypeError,ValueError):
            same=False
        if not same or candidate.completed is not True:
            raise SourceReplayRefused("claimed M15 differs from original source replay")
    return {
        "schema":VERSION,"instrument":{"engine":instrument.engine,"market":instrument.market,
                                        "board":instrument.board,"secid":instrument.secid},
        "m15_from_source":m15,
        "original_m1_rows_used":len(selected),
        "verified_page_metadata":meta,
        "latest_operator_reported_receipt_utc":observed_previous.isoformat(),
        "source_bytes_replayed_and_matching":True,
        "source_transport_independently_authenticated":False,
        "receipt_clock_independently_attested":False,
        "session_calendar_independently_verified":False,
        "level_journal_admission_authorized":False,
        "forecast_or_trade_authorized":False,
        "production_authorized":False,
    }
