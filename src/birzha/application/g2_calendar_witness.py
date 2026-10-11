"""Offline exact-SECID history/candle concordance; never a certified exchange calendar.

Requires a separately received and SHA-verified MOEX ISS /history bundle.
No fallback to guessed weekdays or a caller-declared session calendar.
May be unavailable for unauthenticated/non-subscriber users.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from zoneinfo import ZoneInfo


MOEX_TZ = ZoneInfo("Europe/Moscow")
SCHEMA = "G2_EXACT_SECID_MOEX_HISTORY_WITNESS_V1"


class CalendarWitnessRefused(ValueError):
    pass


def _utc(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CalendarWitnessRefused("invalid calendar receipt UTC") from exc


def _activity(row: dict) -> bool:
    # Do not use the legacy permissive date-only branch; no positive activity
    # field means no evidence of a trading session on this exact instrument.
    return any(
        type(row.get(key)) in (int, float)
        and row[key] > 0 for key in ("NUMTRADES", "VOLUME", "VALUE")
    )


def verify_exact_history_concordance(
    *, instrument: dict, d1_raw_pages: tuple[bytes, ...],
    witness: dict, issue_t0: str,
) -> dict:
    """Check separate /history endpoint for matching recent active D1 dates.

    Raw history pages are separately supplied, each with observed UTC and SHA.
    Historical activity is supporting evidence, NOT a certified schedule,
    exchange-close guarantee, trusted first-receipt or independent provider.
    """
    try:
        engine, market, board, secid = (
            instrument[k] for k in ("engine", "market", "board", "secid")
        )
        if any(not isinstance(v, str) or not v or "/" in v for v in
               (engine, market, board, secid)):
            raise ValueError("invalid exact instrument route")
        if not isinstance(witness, dict) or witness.get("schema") != SCHEMA:
            raise ValueError("untrusted witness type")
        if any(witness.get(k) != val for k,val in (
            ("engine", engine), ("market", market), ("board", board), ("secid", secid)
        )):
            raise ValueError("mismatched original SECID route")
        path = (f"/history/engines/{engine}/markets/{market}/boards/"
                f"{board}/securities/{secid}.json")
        if witness.get("path") != path:
            raise ValueError("history witness not from exact board+SECID endpoint")
        start = date.fromisoformat(witness["requested_from"])
        stop = date.fromisoformat(witness["requested_till"])
        if start > stop:
            raise ValueError("inverted session calendar interval")
        issue = _utc(issue_t0)
        pages = witness["pages"]
        if not isinstance(pages, list) or not 0 < len(pages) <= 20:
            raise ValueError("missing bounded history pages")
        expected_start, last_seen, traded = 0, None, []
        expected_total, page_size = None, None
        for page in pages:
            if not isinstance(page, dict) or page.get("start") != expected_start:
                raise ValueError("noncontiguous history pages")
            blob = page["raw_body"]
            if not isinstance(blob, bytes) or sha256(blob).hexdigest() != page["sha256"]:
                raise ValueError("history page bytes differ from original SHA")
            began, ended = _utc(page["observed_start_utc"]), _utc(page["observed_end_utc"])
            if not began <= ended <= issue or (issue-ended).total_seconds()>300:
                raise ValueError("history page timestamp is stale/noncausal")
            data = json.loads(blob)
            table = data["history"]
            cols, raw_rows = table["columns"], table["data"]
            if (not isinstance(raw_rows, list) or not 0 < len(raw_rows) <= 100
                or any(x not in cols for x in
                       ("TRADEDATE","NUMTRADES","VOLUME","VALUE"))):
                raise ValueError("history activity/TRADEDATE fields not provided")
            cursor = data.get("history.cursor", {})
            ccols, crows = cursor.get("columns", []), cursor.get("data", [])
            if crows:
                c = dict(zip(ccols, crows[0]))
                total, size = int(c["TOTAL"]), int(c["PAGESIZE"])
                if total < len(raw_rows) or not 1 <= size <= 100:
                    raise ValueError("invalid history pagination")
                if expected_total is None:
                    expected_total, page_size = total, size
                elif (expected_total, page_size) != (total, size):
                    raise ValueError("history cursor shifted")
            elif len(raw_rows) >= 100:
                raise ValueError("cannot prove history completion without cursor")
            for item in raw_rows:
                if len(item) != len(cols):
                    raise ValueError("ragged history row")
                row = dict(zip(cols,item))
                day = date.fromisoformat(str(row["TRADEDATE"]))
                if not start <= day <= stop or (last_seen is not None and day < last_seen):
                    raise ValueError("history date outside requested ordering/period")
                if _activity(row):
                    if day in traded:
                        raise ValueError("duplicate active history date")
                    traded.append(day)
                last_seen = day
            expected_start += len(raw_rows)
        if expected_total is not None and expected_start != expected_total:
            raise ValueError("incomplete original history pagination")
        if not traded:
            raise ValueError("no active exact-SECID historical sessions")
        candle_days = set()
        for raw in d1_raw_pages:
            if not isinstance(raw, bytes) or not raw:
                raise ValueError("missing original D1 candle bytes")
            frame = json.loads(raw)["candles"]
            columns = frame["columns"]
            for row in frame["data"]:
                d = dict(zip(columns,row))
                completed = datetime.fromisoformat(str(d["end"]).replace(" ","T"))
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=MOEX_TZ)
                candle_days.add(completed.astimezone(MOEX_TZ).date())
        if not candle_days or max(traded) != max(candle_days):
            raise ValueError("latest active exact-SECID session is not D1 candle endpoint")
        recent = traded[-min(5,len(traded)):]
        if any(day not in candle_days for day in recent):
            raise ValueError("missing recent independently indexed history session in D1")
        if not start <= min(recent) or not max(recent) <= stop:
            raise ValueError("source history coverage mismatch")
        return {
            "schema": "G2_MOEX_EXACT_SECID_HISTORY_CONCORDANCE_V1",
            "status": "MOEX_SECOND_ENDPOINT_ACTIVITY_CONCORDANCE",
            "exact_secid": secid,
            "matched_recent_sessions": len(recent),
            "last_active_history_session": traded[-1].isoformat(),
            "source_type": "SAME_PROVIDER_SEPARATE_ENDPOINT",
            "exchange_schedule_independently_certified": False,
            "completed_session_close_time_independently_verified": False,
            "source_time_independently_attested": False,
            "production_authorized": False,
        }
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, AttributeError) as exc:
        raise CalendarWitnessRefused("exact SECID history/candle witness invalid") from exc
