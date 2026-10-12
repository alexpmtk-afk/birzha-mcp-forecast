"""Q-102: compare an original ISS-style D1 body with the entire claimed window.

A match proves only self-consistency of submitted bytes. It NEVER proves
that MOEX sent those bytes or that this is the authoritative calendar.
A non-ISS synthetic development fixture is explicitly UNREPLAYED.
"""
from __future__ import annotations

from datetime import timedelta
from math import isfinite

from birzha.application.g2_m1_m15_source_replay import (
    _strict_json, _table, _m1_time, SourceReplayRefused,
)

class FutureD1ContentRefused(ValueError):
    """ISS candle contents and the claimed future session window differ."""


def replay_native_d1_if_present(
    raw: bytes, *, dates: tuple[str, ...],
    completed_at: tuple[str, ...], closes: tuple[float, ...],
) -> bool:
    """Returns True iff all original native D1 rows reproduce all claims.

    False means no native ISS data has been supplied (synthetic legacy
    fixture): never count that as a verified provider or verified session.
    """
    if type(raw) is not bytes or len(raw) > 8_000_000 or not raw:
        raise FutureD1ContentRefused("missing or unbounded original D1 bytes")
    try:
        payload = _strict_json(raw)
    except SourceReplayRefused as exc:
        # A malformed response pretending to contain an ISS candles table
        # is never allowed to downgrade into a harmless opaque fixture.
        if raw.lstrip().startswith((b"{", b"[")) or b'"candles"' in raw:
            # A JSON-shaped document with invalid structure/encoding,
            # escaped candle keys or duplicate source fields is NEVER a
            # safe, opaque legacy fixture.
            raise FutureD1ContentRefused("malformed or ambiguous JSON D1 source") from exc
        # Legacy synthetic fixtures are explicitly UNREPLAYED.
        return False
    if "candles" not in payload:
        # ISS-like truncated/renamed candle tables must NOT quietly pass
        # as harmless offline opaque fixtures. Retain the previously
        # documented non-native synthetic fixture for legacy tests.
        if any(key.casefold().startswith(("candle", "candles."))
               for key in payload):
            raise FutureD1ContentRefused("ambiguous or incomplete native D1 source table")
        return False
    try:
        columns, rows = _table(payload, "candles")
        required = ("begin", "end", "open", "close", "high", "low", "volume", "value")
        if (not all(n in columns for n in required)
            or len(rows) != len(dates) or len(rows) != len(closes)
            or len(rows) != len(completed_at)):
            raise FutureD1ContentRefused("native D1 source has missing OHLCV or extra rows")
        indexed = {name: columns.index(name) for name in required}
        from birzha.application.prospective_capture import MOEX_TIMEZONE, _when
        previous = None
        for row, claimed_day, claimed_end, claimed_close in zip(
            rows, dates, completed_at, closes
        ):
            if type(row) is not list or len(row) != len(columns):
                raise FutureD1ContentRefused("invalid native D1 source shape")
            opening = _m1_time(row[indexed["begin"]])
            ending = _m1_time(row[indexed["end"]])
            numbers = {name: row[indexed[name]]
                       for name in ("open", "close", "high", "low", "volume", "value")}
            if (any(type(v) not in (float, int) or not isfinite(v)
                    for v in numbers.values())
                or any(numbers[k] <= 0 for k in ("open", "close", "high", "low"))
                or any(numbers[k] < 0 for k in ("volume", "value"))
                or numbers["low"] > min(numbers["open"], numbers["close"])
                or numbers["high"] < max(numbers["open"], numbers["close"])
                or numbers["low"] > numbers["high"]
                or opening >= ending
                or ending - opening > timedelta(days=1)
                or opening.astimezone(MOEX_TIMEZONE).date().isoformat() != claimed_day
                or (ending.astimezone(MOEX_TIMEZONE).date()
                    - opening.astimezone(MOEX_TIMEZONE).date()).days not in (0, 1)
                or ending != _when(claimed_end)
                or float(numbers["close"]) != float(claimed_close)
                or (previous is not None and opening <= previous)):
                raise FutureD1ContentRefused("native D1 candle disagrees with supplied future window")
            previous = ending
    except (SourceReplayRefused, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise FutureD1ContentRefused("native D1 source failed exact content replay") from exc
    return True
