"""Read the SBProX 8 RI minute-cluster dialect, without changing its cache."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MOSCOW = timezone(timedelta(hours=3), "Europe/Moscow")
SERIES = {"03-26": "RIH6", "06-26": "RIM6", "09-26": "RIU6",
          "12-26": "RIZ6", "03-27": "RIH7"}
FILENAME = re.compile(r"2026-\d{2}-\d{2}\.locchache")


@dataclass(frozen=True)
class Level:
    price: int
    volume: int
    delta: int
    ask: int
    bid: int
    raw: tuple[str, ...]


@dataclass(frozen=True)
class Minute:
    start: datetime
    end: datetime
    series: str
    secid: str
    open: int
    high: int
    low: int
    close: int
    volume: int
    delta: int
    ask: int
    bid: int
    raw: tuple[str, ...]
    levels: tuple[Level, ...]
    line: int


def _int(value: str) -> int:
    if not re.fullmatch(r"-?[0-9]+", value):
        raise ValueError("expected integer")
    return int(value)


def _raw(fields: list[str]) -> tuple[str, ...]:
    if any(not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value) for value in fields):
        raise ValueError("unrecognized numeric field")
    return tuple(fields)


def _sides(volume: int, delta: int, ask: int, bid: int) -> None:
    if min(ask, bid) < 0 or volume != ask + bid or delta != ask - bid:
        raise ValueError("inconsistent Bid/Ask totals")


def parse_line(line: bytes, day: date, number: int) -> Minute:
    head, sep, tail = line.decode("ascii").rstrip("\r\n").partition("*")
    fields = head.split(":")
    if not sep or len(fields) != 45:
        raise ValueError(f"unexpected minute format ({len(fields)} fields)")
    day_text, start_text = fields[0].split(" ")
    if day_text != day.strftime("%d%m%Y"):
        raise ValueError("record date does not match filename")
    if not all(re.fullmatch(r"[0-9]{6}\.[0-9]{3}", t) for t in (start_text, fields[1])):
        raise ValueError("unexpected time format")
    start, end = (
        datetime.combine(day, datetime.strptime(t, "%H%M%S.%f").time(), MOSCOW)
        for t in (start_text, fields[1])
    )
    if start.second or start.microsecond or end != start + timedelta(seconds=59, milliseconds=999):
        raise ValueError("invalid minute boundaries")
    series = fields[2]
    if series not in SERIES:
        raise ValueError(f"unknown RI series {series!r}")
    high, low, opening, close, volume, delta = map(_int, fields[3:9])
    ask, bid = map(_int, fields[10:12])
    if low <= 0 or not (low <= min(opening, close) <= max(opening, close) <= high):
        raise ValueError("invalid OHLC bounds")
    _sides(volume, delta, ask, bid)
    levels = []
    prices = set()
    for raw in tail.split("|") if tail else ():
        items = raw.split(":")
        if len(items) != 11:
            raise ValueError("unexpected price level format")
        price, level_volume, level_delta = map(_int, items[:3])
        level_ask, level_bid = map(_int, items[7:9])
        _sides(level_volume, level_delta, level_ask, level_bid)
        if price in prices or not low <= price <= high:
            raise ValueError("duplicate price or price outside minute bounds")
        prices.add(price)
        levels.append(Level(price, level_volume, level_delta, level_ask, level_bid, _raw(items)))
    if (sum(l.ask for l in levels), sum(l.bid for l in levels)) != (ask, bid):
        raise ValueError("price level totals differ from minute")
    return Minute(start, end, series, SERIES[series], opening, high, low, close,
                  volume, delta, ask, bid, _raw(fields[3:]), tuple(levels), number)


def read_cache(path: Path, digest):
    """Yield rows and hash the exact bytes read. Caller commits only after EOF."""
    if not FILENAME.fullmatch(path.name):
        raise ValueError("unexpected cache filename")
    day = date.fromisoformat(path.stem)
    seen = set()
    with path.open("rb") as stream:
        for number, raw in enumerate(stream, 1):
            digest.update(raw)
            try:
                bar = parse_line(raw, day, number)
                key = (bar.secid, bar.start)
                if key in seen:
                    raise ValueError("duplicate minute key")
                seen.add(key)
                yield bar
            except ValueError as exc:
                raise ValueError(f"line {number}: {exc}") from exc


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
