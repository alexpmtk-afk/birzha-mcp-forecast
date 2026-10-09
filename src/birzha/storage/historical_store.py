"""Persistent historical candle storage for the Historical Data Foundation."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import duckdb

from birzha.domain.market import Candle, CandleSeries, Instrument


@dataclass(frozen=True, slots=True)
class HistoricalCoverage:
    secid: str
    timeframe: str
    first_begin: str | None
    last_end: str | None
    count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "secid": self.secid,
            "timeframe": self.timeframe,
            "first_begin": self.first_begin,
            "last_end": self.last_end,
            "count": self.count,
        }


class HistoricalCandleStore(Protocol):
    """Storage contract shared by local DuckDB and remote YDB backends."""

    def upsert_series(self, series: CandleSeries) -> int: ...

    def coverage(self, secid: str, timeframe: str) -> HistoricalCoverage: ...

    def read(self, instrument: Instrument, timeframe: str, from_date: str, till_date: str) -> CandleSeries: ...

    def stored_trade_dates(self, secid: str, timeframe: str, from_date: str, till_date: str) -> tuple[str, ...]: ...

    def is_verified(self, symbol: str, timeframe: str, from_date: str, till_date: str) -> bool: ...

    def mark_verified(self, symbol: str, timeframe: str, from_date: str, till_date: str) -> None: ...

    def record_sessions(self, symbol: str, secid: str, trade_dates: tuple[str, ...]) -> None: ...

    def stored_sessions(self, symbol: str, from_date: str, till_date: str) -> tuple[str, ...]: ...

    def stored_session_contracts(
        self, symbol: str, from_date: str, till_date: str
    ) -> tuple[tuple[str, str], ...]: ...

    def stored_session_secids(self, symbol: str, trade_date: str) -> tuple[str, ...]: ...

    def stored_instrument(self, secid: str) -> Instrument | None: ...

    def is_session_range_verified(self, symbol: str, from_date: str, till_date: str) -> bool: ...

    def mark_session_range_verified(self, symbol: str, from_date: str, till_date: str) -> None: ...


class DuckDBHistoricalCandleStore:
    """Idempotent candle store keyed by real contract identity + timeframe + begin."""

    storage_scope = "local"

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = duckdb.connect(path)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_candles (
                secid VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                root_symbol VARCHAR,
                board VARCHAR NOT NULL,
                engine VARCHAR NOT NULL,
                market VARCHAR NOT NULL,
                asset_class VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                begin VARCHAR NOT NULL,
                end_time VARCHAR NOT NULL,
                open DOUBLE,
                close DOUBLE,
                high DOUBLE,
                low DOUBLE,
                value DOUBLE,
                volume DOUBLE,
                completed BOOLEAN NOT NULL,
                source VARCHAR NOT NULL,
                available_at VARCHAR,
                available_at_confidence VARCHAR NOT NULL DEFAULT 'UNKNOWN',
                observed_at VARCHAR,
                revision VARCHAR,
                currency VARCHAR,
                tick_size DOUBLE,
                tick_value DOUBLE,
                contract_multiplier DOUBLE,
                expiration_date VARCHAR,
                settlement_date VARCHAR,
                calendar_id VARCHAR,
                session_profile VARCHAR,
                data_capabilities_json VARCHAR,
                roll_policy VARCHAR,
                PRIMARY KEY (secid, timeframe, begin)
            )
        """)
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS available_at VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS available_at_confidence VARCHAR DEFAULT 'UNKNOWN'")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS observed_at VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS revision VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS currency VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS tick_size DOUBLE")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS tick_value DOUBLE")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS contract_multiplier DOUBLE")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS expiration_date VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS settlement_date VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS calendar_id VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS session_profile VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS data_capabilities_json VARCHAR")
        self._connection.execute("ALTER TABLE historical_candles ADD COLUMN IF NOT EXISTS roll_policy VARCHAR")
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_candle_revisions (
                secid VARCHAR NOT NULL,
                timeframe VARCHAR NOT NULL,
                begin VARCHAR NOT NULL,
                observed_at VARCHAR NOT NULL,
                revision VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                PRIMARY KEY (secid, timeframe, begin, revision)
            )
        """)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_verified_ranges (symbol VARCHAR NOT NULL, timeframe VARCHAR NOT NULL, from_date VARCHAR NOT NULL, till_date VARCHAR NOT NULL, PRIMARY KEY (symbol, timeframe, from_date, till_date))
        """)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_sessions (symbol VARCHAR NOT NULL, secid VARCHAR NOT NULL, trade_date VARCHAR NOT NULL, PRIMARY KEY (symbol, secid, trade_date))
        """)
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS historical_session_verified_ranges (symbol VARCHAR NOT NULL, from_date VARCHAR NOT NULL, till_date VARCHAR NOT NULL, PRIMARY KEY (symbol, from_date, till_date))
        """)

    def _stamp_legacy_base_revision(self, series: CandleSeries) -> None:
        """Fingerprint stored legacy OHLC itself, never a newly received payload.

        Legacy rows with revision=NULL have unknown first-receipt time. A later
        backfill may present changed prices; assigning *its* hash to the old
        immutable base falsely identifies that base. This method runs under
        the upsert lock, and intentionally leaves observed_at NULL when no
        original receipt evidence exists.
        """
        for candle in series.candles:
            row = self._connection.execute("""
                SELECT open, close, high, low, value, volume,
                       begin, end_time, completed, source
                FROM historical_candles
                WHERE secid=? AND timeframe=? AND begin=? AND revision IS NULL
            """, [series.instrument.secid, series.timeframe, candle.begin]).fetchone()
            if row is None:
                continue
            original = Candle(
                open=row[0], close=row[1], high=row[2], low=row[3],
                value=row[4], volume=row[5], begin=str(row[6]),
                end=str(row[7]), completed=bool(row[8]), source=str(row[9]),
            )
            base_revision, _ = _candle_revision(original, str(row[9]))
            self._connection.execute("""
                UPDATE historical_candles SET revision=?
                WHERE secid=? AND timeframe=? AND begin=? AND revision IS NULL
            """, [
                base_revision, series.instrument.secid, series.timeframe,
                candle.begin,
            ])

    def upsert_series(self, series: CandleSeries) -> int:
        rows = []
        revision_rows = []
        fallback_observed_at = datetime.now(timezone.utc).isoformat()
        for candle in series.candles:
            source = candle.source or series.source
            revision, payload_json = _candle_revision(candle, source)
            observed_at = candle.observed_at or fallback_observed_at
            rows.append([
                series.instrument.secid,
                series.instrument.symbol,
                series.instrument.root_symbol,
                series.instrument.board,
                series.instrument.engine,
                series.instrument.market,
                series.instrument.asset_class,
                series.timeframe,
                candle.begin,
                candle.end,
                candle.open,
                candle.close,
                candle.high,
                candle.low,
                candle.value,
                candle.volume,
                candle.completed,
                source,
                candle.available_at,
                candle.available_at_confidence,
                observed_at,
                candle.revision or revision,
                series.instrument.currency,
                series.instrument.tick_size,
                series.instrument.tick_value,
                series.instrument.contract_multiplier,
                series.instrument.expiration_date,
                series.instrument.settlement_date,
                series.instrument.calendar_id,
                series.instrument.session_profile,
                json.dumps(list(series.instrument.data_capabilities), ensure_ascii=False),
                series.instrument.roll_policy,
            ])
            revision_rows.append([
                series.instrument.secid,
                series.timeframe,
                candle.begin,
                observed_at,
                candle.revision or revision,
                payload_json,
                source,
                series.instrument.secid,
                series.timeframe,
                candle.begin,
                candle.revision or revision,
            ])
        if not rows:
            return 0
        with self._lock:
            # Make legacy first-version identity consistent *before* comparing
            # the incoming payload. Unknown historical receipt remains NULL.
            self._stamp_legacy_base_revision(series)
            self._connection.executemany("""
                INSERT OR IGNORE INTO historical_candle_revisions
                (secid, timeframe, begin, observed_at, revision, payload_json, source)
                SELECT ?, ?, ?, ?, ?, ?, ?
                WHERE EXISTS (
                    SELECT 1 FROM historical_candles
                    WHERE secid=? AND timeframe=? AND begin=?
                      AND revision IS NOT NULL AND revision<>?
                )
            """, revision_rows)
            self._connection.executemany("""
                INSERT INTO historical_candles
                (secid, symbol, root_symbol, board, engine, market, asset_class, timeframe,
                 begin, end_time, open, close, high, low, value, volume, completed, source,
                 available_at, available_at_confidence, observed_at, revision,
                 currency, tick_size, tick_value, contract_multiplier, expiration_date,
                 settlement_date, calendar_id, session_profile, data_capabilities_json, roll_policy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (secid, timeframe, begin) DO UPDATE SET
                    available_at=COALESCE(historical_candles.available_at, excluded.available_at),
                    available_at_confidence=CASE
                        WHEN historical_candles.available_at IS NOT NULL
                        THEN historical_candles.available_at_confidence
                        ELSE excluded.available_at_confidence
                    END,
                    observed_at=historical_candles.observed_at,
                    revision=COALESCE(historical_candles.revision, excluded.revision)
            """, rows)
        return len(rows)

    def _selected_candles(
        self, secid: str, timeframe: str, from_date: str, till_date: str,
        *, knowledge_cutoff: datetime | None = None,
    ) -> tuple[Candle, ...]:
        """Project one candle per begin from immutable, observed versions.

        Latest prefers finalized versions over any later stale forming update.
        A historical cutoff discards every version lacking a proven receipt
        time, plus completed versions whose event end/known availability is
        later than the cutoff. No unknown legacy receipt is retroactively set.
        """
        with self._lock:
            base_rows = self._connection.execute("""
                SELECT open, close, high, low, value, volume, begin, end_time,
                       completed, source, available_at, available_at_confidence,
                       observed_at, revision
                FROM historical_candles
                WHERE secid=? AND timeframe=? AND begin >= ? AND begin < ?
                ORDER BY begin
            """, [secid, timeframe, from_date,
                  _exclusive_upper_bound(till_date)]).fetchall()
            revisions = self._connection.execute("""
                SELECT begin, observed_at, revision, payload_json, source
                FROM historical_candle_revisions
                WHERE secid=? AND timeframe=? AND begin >= ? AND begin < ?
                ORDER BY begin, observed_at, revision
            """, [secid, timeframe, from_date,
                  _exclusive_upper_bound(till_date)]).fetchall()

        versions: dict[str, list[Candle]] = {}
        for row in base_rows:
            item = Candle(
                open=row[0], close=row[1], high=row[2], low=row[3],
                value=row[4], volume=row[5], begin=str(row[6]),
                end=str(row[7]), completed=bool(row[8]), source=str(row[9]),
                available_at=str(row[10]) if row[10] is not None else None,
                available_at_confidence=(
                    str(row[11]) if row[11] is not None else "UNKNOWN"
                ),
                observed_at=str(row[12]) if row[12] is not None else None,
                revision=str(row[13]) if row[13] is not None else None,
            )
            versions.setdefault(item.begin, []).append(item)
        for begin, observed_at, revision, payload_json, source in revisions:
            payload = json.loads(str(payload_json))
            if not isinstance(payload, dict) or payload.get("begin") != str(begin):
                raise ValueError("revision payload does not match stored candle begin")
            if str(begin) not in versions:
                raise ValueError("orphan candle revision without base version")
            if payload.get("source") != source:
                raise ValueError("revision payload and source disagree")
            item = Candle(
                open=payload["open"], close=payload["close"],
                high=payload["high"], low=payload["low"],
                value=payload["value"], volume=payload["volume"],
                begin=str(begin), end=str(payload["end"]),
                completed=payload["completed"] is True, source=str(source),
                # The revisions table does not store version-specific
                # available_at; do not borrow the base version's value.
                available_at=None, available_at_confidence="UNKNOWN",
                observed_at=str(observed_at), revision=str(revision),
            )
            versions[item.begin].append(item)

        answer: list[Candle] = []
        for begin in sorted(versions):
            candidates: list[tuple[Candle, datetime | None]] = []
            for item in versions[begin]:
                observed = _receipt_utc(item.observed_at)
                if knowledge_cutoff is not None:
                    # No legacy/inferred historical first-receipt promotion.
                    if observed is None or observed > knowledge_cutoff:
                        continue
                    if item.completed:
                        try:
                            if _exchange_timestamp_utc(item.end) > knowledge_cutoff:
                                continue
                            if (item.available_at is not None
                                and _exchange_timestamp_utc(item.available_at) > knowledge_cutoff):
                                continue
                        except ValueError:
                            continue
                candidates.append((item, observed))
            if not candidates:
                continue
            min_receipt = datetime.min.replace(tzinfo=timezone.utc)
            rank = lambda pair: (
                int(pair[0].completed),
                pair[1] or min_receipt,
            )
            best_rank = max(rank(pair) for pair in candidates)
            winners = [pair for pair in candidates if rank(pair) == best_rank]
            # Two distinct payloads observed at exactly the same instant
            # cannot be ordered as a causal version. Fail closed.
            if knowledge_cutoff is not None and len({
                _candle_revision(pair[0], pair[0].source or "")[0]
                for pair in winners
            }) > 1:
                raise ValueError("ambiguous same-timestamp historical candle versions")
            winner = sorted(winners, key=lambda pair: pair[0].revision or "")[-1][0]
            answer.append(winner)
        return tuple(answer)

    def _has_revisions(self, secid: str, timeframe: str) -> bool:
        with self._lock:
            return self._connection.execute("""
                SELECT 1 FROM historical_candle_revisions
                WHERE secid=? AND timeframe=? LIMIT 1
            """, [secid, timeframe]).fetchone() is not None

    def coverage(self, secid: str, timeframe: str) -> HistoricalCoverage:
        if self._has_revisions(secid, timeframe):
            candles = tuple(
                candle for candle in self._selected_candles(
                    secid, timeframe, "0001-01-01", "9999-12-31"
                ) if candle.completed
            )
            return HistoricalCoverage(
                secid, timeframe,
                min((item.begin for item in candles), default=None),
                max((item.end for item in candles), default=None),
                len(candles),
            )
        with self._lock:
            row = self._connection.execute("""
                SELECT min(begin), max(end_time), count(*)
                FROM historical_candles
                WHERE secid=? AND timeframe=? AND completed=true
            """, [secid, timeframe]).fetchone()
        if not row or int(row[2]) == 0:
            return HistoricalCoverage(secid, timeframe, None, None, 0)
        return HistoricalCoverage(
            secid, timeframe, str(row[0]), str(row[1]), int(row[2])
        )

    def read(
        self, instrument: Instrument, timeframe: str,
        from_date: str, till_date: str,
    ) -> CandleSeries:
        """Latest projected content, for current/reconstructed analysis only."""
        candles = self._selected_candles(
            instrument.secid, timeframe, from_date, till_date
        )
        return CandleSeries(
            instrument=instrument, timeframe=timeframe, candles=candles,
            source=candles[0].source or instrument.source if candles else instrument.source,
        )

    def read_as_of(
        self, instrument: Instrument, timeframe: str,
        from_date: str, till_date: str, *, knowledge_cutoff: str,
    ) -> CandleSeries:
        """Fail-closed, timestamped receipts; not an assertion of vintage quality.

        Callers must use this explicitly for historical decision replay. Old
        rows without first-receipt evidence do not qualify and return no bar.
        """
        cutoff = _receipt_utc(knowledge_cutoff)
        if cutoff is None:
            raise ValueError("knowledge_cutoff requires an explicit timezone")
        candles = self._selected_candles(
            instrument.secid, timeframe, from_date, till_date,
            knowledge_cutoff=cutoff,
        )
        return CandleSeries(
            instrument=instrument, timeframe=timeframe, candles=candles,
            source=candles[0].source or instrument.source if candles else instrument.source,
        )

    def stored_trade_dates(
        self, secid: str, timeframe: str, from_date: str, till_date: str,
    ) -> tuple[str, ...]:
        if self._has_revisions(secid, timeframe):
            selected = self._selected_candles(
                secid, timeframe, from_date, till_date
            )
            return tuple(sorted({
                candle.begin[:10] for candle in selected if candle.completed
            }))
        with self._lock:
            rows = self._connection.execute("""
                SELECT DISTINCT substr(begin, 1, 10) AS trade_date
                FROM historical_candles
                WHERE secid=? AND timeframe=? AND completed=true
                  AND begin >= ? AND begin < ?
                ORDER BY trade_date
            """, [secid, timeframe, from_date,
                  _exclusive_upper_bound(till_date)]).fetchall()
        return tuple(str(row[0]) for row in rows)

    def is_verified(self, symbol: str, timeframe: str, from_date: str, till_date: str) -> bool:
        with self._lock:
            row=self._connection.execute("SELECT 1 FROM historical_verified_ranges WHERE symbol=? AND timeframe=? AND from_date<=? AND till_date>=? LIMIT 1",[symbol,timeframe,from_date,till_date]).fetchone()
        return row is not None

    def mark_verified(self, symbol: str, timeframe: str, from_date: str, till_date: str) -> None:
        with self._lock:
            self._connection.execute("INSERT OR IGNORE INTO historical_verified_ranges VALUES (?,?,?,?)",[symbol,timeframe,from_date,till_date])

    def record_sessions(self, symbol: str, secid: str, trade_dates: tuple[str, ...]) -> None:
        if not trade_dates:
            return
        with self._lock:
            self._connection.executemany(
                "INSERT OR IGNORE INTO historical_sessions VALUES (?,?,?)",
                [[symbol, secid, item[:10]] for item in trade_dates],
            )

    def stored_sessions(self, symbol: str, from_date: str, till_date: str) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT trade_date FROM historical_sessions WHERE symbol=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
                [symbol, from_date[:10], till_date[:10]],
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def stored_session_contracts(
        self, symbol: str, from_date: str, till_date: str
    ) -> tuple[tuple[str, str], ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT trade_date, secid FROM historical_sessions WHERE symbol=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date, secid",
                [symbol, from_date[:10], till_date[:10]],
            ).fetchall()
        return tuple((str(row[0]), str(row[1])) for row in rows)

    def stored_session_secids(self, symbol: str, trade_date: str) -> tuple[str, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT secid FROM historical_sessions WHERE symbol=? AND trade_date=? ORDER BY secid",
                [symbol, trade_date[:10]],
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def stored_instrument(self, secid: str) -> Instrument | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT symbol, root_symbol, board, engine, market, asset_class, source,
                       currency, tick_size, tick_value, contract_multiplier,
                       expiration_date, settlement_date, calendar_id, session_profile,
                       data_capabilities_json, roll_policy
                FROM historical_candles
                WHERE secid=?
                ORDER BY begin DESC
                LIMIT 1
                """,
                [secid],
            ).fetchone()
        if row is None:
            return None
        return Instrument(
            symbol=str(row[0]),
            secid=secid,
            board=str(row[2]),
            engine=str(row[3]),
            market=str(row[4]),
            asset_class=str(row[5]),  # type: ignore[arg-type]
            root_symbol=str(row[1]) if row[1] is not None else None,
            source=str(row[6]),
            currency=str(row[7]) if row[7] is not None else None,
            tick_size=float(row[8]) if row[8] is not None else None,
            tick_value=float(row[9]) if row[9] is not None else None,
            contract_multiplier=float(row[10]) if row[10] is not None else None,
            expiration_date=str(row[11]) if row[11] is not None else None,
            settlement_date=str(row[12]) if row[12] is not None else None,
            calendar_id=str(row[13]) if row[13] is not None else None,
            session_profile=str(row[14]) if row[14] is not None else None,
            data_capabilities=tuple(
                json.loads(str(row[15])) if row[15] is not None else []
            ),
            roll_policy=str(row[16]) if row[16] is not None else None,
        )

    def is_session_range_verified(self, symbol: str, from_date: str, till_date: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM historical_session_verified_ranges WHERE symbol=? AND from_date<=? AND till_date>=? LIMIT 1",
                [symbol, from_date[:10], till_date[:10]],
            ).fetchone()
        return row is not None

    def mark_session_range_verified(self, symbol: str, from_date: str, till_date: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO historical_session_verified_ranges VALUES (?,?,?)",
                [symbol, from_date[:10], till_date[:10]],
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()



def _receipt_utc(value: str | None) -> datetime | None:
    """Only an explicit timezone proves a comparable first-receipt instant."""
    if value is None:
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if instant.tzinfo is None:
        return None
    return instant.astimezone(timezone.utc)


def _exchange_timestamp_utc(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=ZoneInfo("Europe/Moscow"))
    return instant.astimezone(timezone.utc)


def _exclusive_upper_bound(till_date: str) -> str:
    if len(till_date) == 10:
        return till_date + "T23:59:59.999999"
    return till_date



def _candle_revision(candle: Candle, source: str) -> tuple[str, str]:
    payload = {
        "begin": candle.begin,
        "end": candle.end,
        "open": candle.open,
        "close": candle.close,
        "high": candle.high,
        "low": candle.low,
        "value": candle.value,
        "volume": candle.volume,
        "completed": candle.completed,
        "source": source,
    }
    payload_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest(), payload_json
