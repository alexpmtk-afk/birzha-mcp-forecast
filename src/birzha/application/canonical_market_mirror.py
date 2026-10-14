"""Append-only reconciliation of literal canonical candle rows.

No source collection, calendar inference, Forecast admission or scheduler activation.
Callers must hold exclusive ownership of the entire destination workbook.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from birzha.application.market_mirror import D1_COLUMNS
from birzha.storage.google_sheets_bridge import GoogleSheetsBridge

MAX_ROWS = 100_000
MAX_COLUMNS = 40
TIMEFRAMES = frozenset({"D1", "H1", "M15"})


class MirrorConflict(RuntimeError):
    pass


def digest(rows: list[list[object]]) -> str:
    # Google stores numbers as doubles; integral 100.0 and JSON 100 have the
    # same numeric value. This normalises hash representation only, not prices.
    normalized = [[int(x) if isinstance(x, float) and math.isfinite(x) and x.is_integer() else x for x in row] for row in rows]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def rectangular(rows: list[list[object]]) -> list[list[object]]:
    if not rows or not rows[0] or len(rows) > MAX_ROWS:
        raise MirrorConflict("missing header or row limit exceeded")
    header = rows[0]
    width = len(header)
    if width > MAX_COLUMNS or not all(isinstance(x, str) and x for x in header) or len(set(header)) != width:
        raise MirrorConflict("invalid header")
    if not set(D1_COLUMNS).issubset(header):
        raise MirrorConflict("required canonical columns missing")
    result = []
    for row in rows:
        if len(row) > width:
            raise MirrorConflict("row exceeds header width")
        # The native values API omits trailing empty cells. Do not turn missing values into zero.
        normalized = ["" if x is None else x for x in row] + [""] * (width - len(row))
        for x in normalized:
            if not isinstance(x, (str, bool, int, float)) or isinstance(x, float) and not math.isfinite(x):
                raise MirrorConflict("unsupported or nonfinite cell")
            if isinstance(x, str) and len(x) > 2000:
                raise MirrorConflict("cell text exceeds bounded evidence limit")
            if isinstance(x, str) and x.startswith("="):
                raise MirrorConflict("formulas are not candle evidence")
        result.append(normalized)
    return result


def index_rows(rows: list[list[object]]) -> dict[str, list[object]]:
    header = rows[0]
    key_col = header.index("record_key")
    found = {}
    for row in rows[1:]:
        key = row[key_col]
        if not isinstance(key, str) or not key.strip() or key in found:
            raise MirrorConflict("empty or duplicate record_key")
        prices = [row[header.index(x)] for x in ("open", "high", "low", "close")]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in prices):
            raise MirrorConflict("invalid OHLC")
        o, h, low, c = prices
        if not low <= min(o, c) <= max(o, c) <= h:
            raise MirrorConflict("inconsistent OHLC")
        volume = row[header.index("volume")]
        if volume != "" and (isinstance(volume, bool) or not isinstance(volume, (int, float)) or volume < 0):
            raise MirrorConflict("invalid volume")
        found[key] = row
    return found


def validate_identity(rows: list[list[object]], symbol: str, timeframe: str, *, incoming: bool) -> None:
    import re
    header = rows[0]
    patterns = {"SI": r"Si[A-Z]\d{1,2}", "BR": r"BR[A-Z]\d{1,2}", "GOLD": r"GD[A-Z]\d{1,2}"}
    for row in rows[1:]:
        code = row[header.index("contract_code")]
        valid = bool(re.fullmatch(patterns[symbol], str(code))) if symbol in patterns else code == symbol
        if not valid:
            raise MirrorConflict("wrong instrument / exact contract required")
        key = row[header.index("record_key")]
        if len(key.split("|")) != 4 or key.split("|")[0].upper() != symbol or key.split("|")[1] != timeframe or key.split("|")[2] != code or key.split("|")[3] != row[header.index("bar_start_time")]:
            raise MirrorConflict("record_key does not match identity")
        if incoming and timeframe in {"H1", "M15"}:
            try:
                start = datetime.fromisoformat(str(row[header.index("bar_start_time")]).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(row[header.index("bar_end_time")]).replace("Z", "+00:00"))
            except ValueError as exc:
                raise MirrorConflict("invalid intraday timestamp") from exc
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise MirrorConflict("intraday interval requires explicit timezone and positive duration")
            if row[header.index("is_complete")] not in (True, False) or not isinstance(row[header.index("is_complete")], bool):
                raise MirrorConflict("is_complete must retain a boolean source flag")
            if "research_status" in header:
                status = str(row[header.index("research_status")])
                if status not in {"RECONSTRUCTED_RESEARCH", "INDIVIDUAL_BAR_VALID_SESSION_UNCERTIFIED"} and not status.startswith("RESEARCH_ONLY_"):
                    raise MirrorConflict("historical intraday must remain explicitly research-only")
            confidence = row[header.index("available_at_confidence")]
            available = row[header.index("available_at")]
            if confidence in {"UNKNOWN", "UNVERIFIED", "NOT_PIT_VERIFIED"}:
                if available != "":
                    raise MirrorConflict("unknown availability must remain empty")
            elif confidence == "INFERRED":
                if available != row[header.index("bar_end_time")] or "caveat" not in header or "PIT_NOT_ATTESTED" not in str(row[header.index("caveat")]):
                    raise MirrorConflict("inferred bar end requires explicit PIT limitation")
            else:
                raise MirrorConflict("historical availability cannot be promoted to verified")
            if row[header.index("trade_session_date")] != "" or row[header.index("session_id")] != "":
                raise MirrorConflict("new historical intraday requires unverified session fields empty")


@dataclass(frozen=True)
class AppendPlan:
    symbol: str
    timeframe: str
    before_sha256: str
    after_sha256: str
    before: list[list[object]]
    additions: list[list[object]]
    initialise_header: bool = False


def plan_append(*, symbol: str, timeframe: str, existing: list[list[object]], incoming: list[list[object]]) -> AppendPlan:
    symbol, _ = GoogleSheetsBridge.target_for_symbol(symbol)
    if timeframe not in TIMEFRAMES:
        raise MirrorConflict("unsupported timeframe")
    source = rectangular(incoming)
    initialise = not existing
    before = rectangular(existing) if existing else [source[0][:]]
    if before[0] != source[0]:
        raise MirrorConflict("schema mismatch; explicit migration required")
    old, new = index_rows(before), index_rows(source)
    validate_identity(before, symbol, timeframe, incoming=False)
    validate_identity(source, symbol, timeframe, incoming=False)
    additions = []
    for key, row in new.items():
        if key in old:
            # bool is not equivalent to numeric 0/1 in evidence fields.
            if any(a != b or isinstance(a, bool) != isinstance(b, bool) for a, b in zip(row, old[key])):
                raise MirrorConflict("existing key has different values; history cannot be overwritten")
        else:
            additions.append(row)
    validate_identity([source[0], *additions], symbol, timeframe, incoming=True)
    if len(before) + len(additions) > MAX_ROWS:
        raise MirrorConflict("combined row limit exceeded")
    actual_before = [] if initialise else before
    writes = [source[0], *additions] if initialise else additions
    return AppendPlan(symbol, timeframe, digest(actual_before), digest(actual_before + writes), actual_before, writes, initialise)


def _read_snapshot(rows: list[list[object]]) -> list[list[object]]:
    return rectangular(rows) if rows else []


class CanonicalTransport(Protocol):
    def validate_target(self, symbol: str, timeframe: str) -> None: ...
    def read_rows(self, symbol: str, timeframe: str) -> list[list[object]]: ...
    def append_rows(self, symbol: str, timeframe: str, rows: list[list[object]]) -> None: ...


@dataclass
class CanonicalMirrorPublisher:
    transport: CanonicalTransport
    chunk_rows: int = 500

    def sync(self, *, symbol: str, timeframe: str, incoming: list[list[object]], exclusive_workbook_owner: bool = False) -> dict[str, object]:
        if exclusive_workbook_owner is not True:
            raise MirrorConflict("exclusive workbook ownership must be established before publishing")
        symbol, _ = GoogleSheetsBridge.target_for_symbol(symbol)
        if timeframe not in TIMEFRAMES or not 1 <= self.chunk_rows <= 1000:
            raise MirrorConflict("invalid timeframe or chunk size")
        self.transport.validate_target(symbol, timeframe)
        plan = plan_append(symbol=symbol, timeframe=timeframe, existing=self.transport.read_rows(symbol, timeframe), incoming=incoming)
        if len(plan.additions) > 2000:
            raise MirrorConflict("more than 2000 additions: split approved source into bounded batches before publishing")
        expected = plan.before
        for offset in range(0, len(plan.additions), self.chunk_rows):
            self.transport.validate_target(symbol, timeframe)
            if digest(_read_snapshot(self.transport.read_rows(symbol, timeframe))) != digest(expected):
                raise MirrorConflict("destination changed after preflight")
            chunk = plan.additions[offset:offset + self.chunk_rows]
            # Never blindly retry a write: a lost response may follow a successful commit.
            self.transport.append_rows(symbol, timeframe, chunk)
            expected = expected + chunk
            if digest(_read_snapshot(self.transport.read_rows(symbol, timeframe))) != digest(expected):
                raise MirrorConflict("post-write full readback mismatch; resume by fresh reconciliation")
        self.transport.validate_target(symbol, timeframe)
        if digest(_read_snapshot(self.transport.read_rows(symbol, timeframe))) != plan.after_sha256:
            raise MirrorConflict("final readback mismatch")
        return {"status": "MIRROR_READBACK_PASS", "symbol": symbol, "timeframe": timeframe,
                "added_rows": len(plan.additions) - int(plan.initialise_header), "data_rows": len(expected) - 1,
                "sha256": plan.after_sha256, "historical_prices_unchanged": True,
                "scope": "MIRROR_ONLY; NOT_CALENDAR_OR_PIT_OR_FORECAST_ACCEPTANCE"}