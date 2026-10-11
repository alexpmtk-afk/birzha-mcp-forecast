"""Isolated, opt-in M15 original-receipt level event pilot. No live collector."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Callable
from birzha.domain.market import Candle, Instrument

VERSION = "G2_LEVEL_EVENT_PILOT_V1"


class Refused(ValueError):
    """Unsafe, unobserved, or retroactively supplied market input."""


class Collision(RuntimeError):
    """An immutable receipt or hash-chain identity was altered."""


def utc(value):
    try:
        x = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if x.tzinfo is None or x.utcoffset() is None:
            raise ValueError("naive")
        return x.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise Refused("offset-aware timestamp required") from exc


def stamp(x):
    return x.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))


def digest(raw):
    return sha256(raw).hexdigest()


def chained(previous, payload):
    return digest((previous + "\n" + payload).encode("utf8"))


class LevelEventPilot:
    """Append one freshly received completed M15, never historic bulk data.

    Caller reports source bytes and observed_at; neither proves original
    MOEX provenance. Only first_received_at is assigned by this process.
    """
    def __init__(self, path: str | Path, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.path = Path(path)
        if str(self.path) == ":memory:" or self.path.suffix != ".sqlite3":
            raise Refused("explicit isolated .sqlite3 path required")
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), isolation_level=None, timeout=5)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA foreign_keys=ON")
        old = {x[0] for x in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'")}
        original_triggers = {x[0] for x in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        required_tables = {"level_meta","level_receipts","level_events"}
        required_triggers = {"level_receipts_no_update", "level_receipts_no_delete",
                             "level_events_no_update", "level_events_no_delete"}
        # Never silently rebuild a dropped table or missing append-only
        # trigger in an existing database. Empty DB is the only init path.
        if old and (old != required_tables
                    or not required_triggers.issubset(original_triggers)):
            self.db.close()
            raise Refused("incomplete, foreign, or weakened existing journal")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS level_meta(version TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS level_receipts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream TEXT NOT NULL, bar_start TEXT NOT NULL,
                bar_end TEXT NOT NULL, observed TEXT NOT NULL,
                received TEXT NOT NULL, bar_json TEXT NOT NULL,
                raw BLOB NOT NULL, raw_sha TEXT NOT NULL,
                previous_hash TEXT NOT NULL, row_hash TEXT NOT NULL,
                UNIQUE(stream,bar_end));
            CREATE TABLE IF NOT EXISTS level_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id INTEGER NOT NULL REFERENCES level_receipts(id),
                stream TEXT NOT NULL, role TEXT NOT NULL,
                kind TEXT NOT NULL, price REAL NOT NULL,
                payload TEXT NOT NULL, previous_hash TEXT NOT NULL,
                row_hash TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS level_receipts_no_update
              BEFORE UPDATE ON level_receipts
              BEGIN SELECT RAISE(ABORT,'append only receipts'); END;
            CREATE TRIGGER IF NOT EXISTS level_receipts_no_delete
              BEFORE DELETE ON level_receipts
              BEGIN SELECT RAISE(ABORT,'append only receipts'); END;
            CREATE TRIGGER IF NOT EXISTS level_events_no_update
              BEFORE UPDATE ON level_events
              BEGIN SELECT RAISE(ABORT,'append only events'); END;
            CREATE TRIGGER IF NOT EXISTS level_events_no_delete
              BEFORE DELETE ON level_events
              BEGIN SELECT RAISE(ABORT,'append only events'); END;
        """)
        self.db.execute("INSERT OR IGNORE INTO level_meta VALUES (?)", (VERSION,))
        if self.db.execute("SELECT version FROM level_meta").fetchall() != [(VERSION,)]:
            self.db.close()
            raise Refused("schema version mismatch")
        required = {"level_receipts_no_update", "level_receipts_no_delete",
                    "level_events_no_update", "level_events_no_delete"}
        triggers = {x[0] for x in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        if not required.issubset(triggers):
            self.db.close()
            raise Refused("missing immutability triggers")
        self.verify()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _event(self, receipt, stream, role, kind, price, now, atr):
        last = self.db.execute(
            "SELECT row_hash FROM level_events ORDER BY id DESC LIMIT 1").fetchone()
        previous = last[0] if last else "GENESIS"
        payload = canonical({
            "version": VERSION, "receipt_id": receipt, "stream": stream,
            "role": role, "kind": kind, "price": price, "first_received_at": now,
            "atr_unverified": atr, "recorded_as_it_arrived": True,
            "external_source_authenticated": False, "role_flip_verified": False,
            "scenario_authorized": False, "production_authorized": False})
        self.db.execute(
            "INSERT INTO level_events(receipt_id,stream,role,kind,price,payload,previous_hash,row_hash) VALUES (?,?,?,?,?,?,?,?)",
            (receipt, stream, role, kind, price, payload, previous,
             chained(previous, payload)))

    def append(self, instrument: Instrument, bar: Candle, *,
               original_source_bytes: bytes, atr_price_scale: float):
        if not isinstance(instrument, Instrument) or not isinstance(bar, Candle):
            raise TypeError("Instrument and Candle contracts required")
        parts = [instrument.symbol, instrument.secid, instrument.board,
                 instrument.engine, instrument.market]
        if any(not isinstance(x, str) or not x.strip() for x in parts):
            raise Refused("exact market and contract identity required")
        stream = canonical(parts)
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise Refused("timezone-aware receipt clock required")
        now = now.astimezone(timezone.utc)
        if (bar.completed is not True or bar.available_at_confidence != "EXACT"
            or not isinstance(bar.source, str) or not bar.source):
            raise Refused("partial, uncertified or unidentified candle")
        start, end, avail, observed = (
            utc(bar.begin), utc(bar.end), utc(bar.available_at), utc(bar.observed_at))
        if (end-start != timedelta(minutes=15) or
            not start < end <= avail <= observed <= now):
            raise Refused("noncausal M15 receipt")
        values = [bar.open, bar.high, bar.low, bar.close, bar.volume]
        if (any(type(v) not in (float,int) or not math.isfinite(v) for v in values)
            or min(values[:4]) <= 0 or bar.volume < 0
            or not bar.low <= bar.open <= bar.high
            or not bar.low <= bar.close <= bar.high):
            raise Refused("invalid OHLCV")
        if (not isinstance(original_source_bytes, bytes) or not original_source_bytes
            or type(atr_price_scale) not in (float,int)
            or not math.isfinite(atr_price_scale) or atr_price_scale <= 0):
            raise Refused("source bytes and positive ATR required")
        bar_json = canonical(bar.to_dict())
        raw_sha = digest(original_source_bytes)
        received = stamp(now)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            duplicate = self.db.execute(
                "SELECT id,bar_json,raw_sha,received FROM level_receipts WHERE stream=? AND bar_end=?",
                (stream, stamp(end))).fetchone()
            if duplicate:
                if duplicate[1] != bar_json or duplicate[2] != raw_sha:
                    raise Collision("same bar identity has different content")
                self.db.execute("COMMIT")
                return {"status":"DUPLICATE_IDENTICAL", "receipt_id":duplicate[0],
                        "first_received_at":duplicate[3], "new_events":0}
            # Only NEW receipts must be fresh. A later identical retry must
            # remain idempotent after restart; it cannot backdate a new row.
            if ((now-observed).total_seconds() > 300 or
                (now-end).total_seconds() > 900):
                raise Refused("stale M15 receipt; historical backfill forbidden")
            last = self.db.execute(
                "SELECT bar_start,bar_end,observed,bar_json FROM level_receipts WHERE stream=? ORDER BY id DESC LIMIT 1",
                (stream,)).fetchone()
            if last and (start < utc(last[1]) or end <= utc(last[1])
                         or observed <= utc(last[2])
                         or bar.source != json.loads(last[3])["source"]):
                raise Refused("late/backfilled, overlapping or mixed-source M15")
            global_last = self.db.execute(
                "SELECT row_hash,received FROM level_receipts ORDER BY id DESC LIMIT 1").fetchone()
            if global_last and now < utc(global_last[1]):
                raise Refused("clock moved backwards")
            previous = global_last[0] if global_last else "GENESIS"
            envelope = canonical({
                "stream":stream, "bar":json.loads(bar_json), "raw_sha":raw_sha,
                "received":received, "version":VERSION})
            cur = self.db.execute(
                "INSERT INTO level_receipts(stream,bar_start,bar_end,observed,received,bar_json,raw,raw_sha,previous_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (stream,stamp(start),stamp(end),stamp(observed),received,
                 bar_json,original_source_bytes,raw_sha,previous,
                 chained(previous,envelope)))
            seq = cur.lastrowid
            before = self.db.execute("SELECT COUNT(*) FROM level_events").fetchone()[0]
            if last and start > utc(last[1]):
                # A gap terminates old candidate levels. Old research levels
                # must not be retested as if the new segment were continuous.
                for role in ("SUPPORT", "RESISTANCE"):
                    active = self.db.execute(
                        "SELECT kind,price FROM level_events WHERE stream=? AND role=? ORDER BY id DESC LIMIT 1",
                        (stream,role)).fetchone()
                    if active and active[0] != "LEVEL_HISTORY_GAP":
                        self._event(seq,stream,role,"LEVEL_HISTORY_GAP",
                                    active[1],received,atr_price_scale)
            last20 = self.db.execute(
                "SELECT bar_start,bar_end,bar_json FROM level_receipts WHERE stream=? ORDER BY id DESC LIMIT 20",
                (stream,)).fetchall()[::-1]
            continuous = (len(last20)==20 and
                all(last20[i][0]==last20[i-1][1] for i in range(1,20)))
            if continuous:
                candles = [json.loads(x[2]) for x in last20]
                levels = {"SUPPORT":min(x["low"] for x in candles),
                          "RESISTANCE":max(x["high"] for x in candles)}
                for role, price in levels.items():
                    previous_event = self.db.execute(
                        "SELECT kind,price,receipt_id FROM level_events WHERE stream=? AND role=? ORDER BY id DESC LIMIT 1",
                        (stream,role)).fetchone()
                    if (not previous_event or
                        previous_event[0] == "LEVEL_HISTORY_GAP" or
                        previous_event[1] != price):
                        kind = ("LEVEL_FIRST_DETECTED"
                                if not previous_event or previous_event[0] == "LEVEL_HISTORY_GAP"
                                else "LEVEL_PRICE_UPDATED")
                        self._event(seq,stream,role,kind,price,received,atr_price_scale)
                    else:
                        held = ((bar.low <= price + .15*atr_price_scale and
                                 bar.close >= price + .25*atr_price_scale)
                                if role=="SUPPORT" else
                                (bar.high >= price - .15*atr_price_scale and
                                 bar.close <= price - .25*atr_price_scale))
                        if held and (previous_event[0] != "LEVEL_RETEST_HELD"
                                     or seq - previous_event[2] >= 2):
                            self._event(seq,stream,role,"LEVEL_RETEST_HELD",
                                        price,received,atr_price_scale)
            new_count = self.db.execute("SELECT COUNT(*) FROM level_events").fetchone()[0]-before
            self.db.execute("COMMIT")
            return {"status":"NEW_RECEIPT", "receipt_id":seq,
                    "first_received_at":received, "new_events":new_count,
                    "history_ready":bool(continuous),
                    "source_independently_authenticated":False,
                    "production_authorized":False}
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def events(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise Refused("bounded event read required")
        rows = self.db.execute(
            "SELECT payload FROM level_events ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [json.loads(x[0]) for x in rows[::-1]]

    def verify(self):
        """Reconcile every indexed column and event FK with hashed payloads.

        SHA chains detect accidental/local corruption, not coordinated
        rehashing by an attacker controlling the entire unanchored database.
        """
        previous = "GENESIS"
        receipts = {}
        streams = {}
        rows = self.db.execute(
            "SELECT id,stream,bar_start,bar_end,observed,received,"
            "bar_json,raw,raw_sha,previous_hash,row_hash "
            "FROM level_receipts ORDER BY id").fetchall()
        try:
            for seq,stream,start,end,observed,received,bar_json,raw,rawsha,parent,rowhash in rows:
                bar = json.loads(bar_json)
                # The denormalized index/timestamp columns must agree with
                # the immutable record they purportedly index.
                if (not isinstance(bar,dict) or canonical(bar)!=bar_json
                    or not isinstance(json.loads(stream),list)
                    or len(json.loads(stream))!=5
                    or start!=stamp(utc(bar["begin"]))
                    or end!=stamp(utc(bar["end"]))
                    or observed!=stamp(utc(bar["observed_at"]))
                    or stamp(utc(received))!=received
                    or not utc(bar["end"]) <= utc(bar["observed_at"]) <= utc(received)
                    or seq in receipts or (stream,end) in streams):
                    raise Collision("receipt indexed fields not bound to hashed candle")
                previous_for_stream = streams.get(stream)
                if previous_for_stream is not None:
                    if (utc(start)<utc(previous_for_stream[0])
                        or utc(end)<=utc(previous_for_stream[0])
                        or utc(observed)<=utc(previous_for_stream[1])):
                        raise Collision("receipt stream chronology corrupted")
                if not isinstance(raw,bytes) or digest(raw)!=rawsha or parent!=previous:
                    raise Collision("original source or receipt chain altered")
                payload=canonical({"stream":stream,"bar":bar,"raw_sha":rawsha,
                                   "received":received,"version":VERSION})
                if rowhash!=chained(previous,payload):
                    raise Collision("receipt digest changed")
                streams[stream]=(end,observed)
                receipts[seq]=(stream,received)
                previous=rowhash
            previous="GENESIS"
            last_event_receipt=0
            latest_roles={}
            event_rows=self.db.execute(
                "SELECT id,receipt_id,stream,role,kind,price,payload,"
                "previous_hash,row_hash FROM level_events ORDER BY id").fetchall()
            for eid,receipt_id,stream,role,kind,price,payload,parent,rowhash in event_rows:
                event=json.loads(payload)
                identity=receipts.get(receipt_id)
                if (identity is None or canonical(event)!=payload
                    or type(price) not in (int,float) or not math.isfinite(price)
                    or price <= 0
                    or event.get("version")!=VERSION
                    or event.get("receipt_id")!=receipt_id
                    or event.get("stream")!=stream or identity[0]!=stream
                    or event.get("role")!=role
                    or role not in ("SUPPORT","RESISTANCE")
                    or event.get("kind")!=kind
                    or kind not in ("LEVEL_FIRST_DETECTED","LEVEL_PRICE_UPDATED",
                                    "LEVEL_RETEST_HELD","LEVEL_HISTORY_GAP")
                    or event.get("price")!=price
                    or event.get("first_received_at")!=identity[1]
                    or event.get("recorded_as_it_arrived") is not True
                    or event.get("external_source_authenticated") is not False
                    or event.get("role_flip_verified") is not False
                    or event.get("scenario_authorized") is not False
                    or event.get("production_authorized") is not False
                    or receipt_id<last_event_receipt):
                    raise Collision("event/index/receipt association corrupted")
                last_event_receipt=receipt_id
                prior=latest_roles.get((stream,role))
                if ((kind=="LEVEL_FIRST_DETECTED" and prior is not None
                     and prior!="LEVEL_HISTORY_GAP")
                    or (kind!="LEVEL_FIRST_DETECTED" and prior is None)
                    or (kind in ("LEVEL_RETEST_HELD","LEVEL_PRICE_UPDATED")
                        and prior=="LEVEL_HISTORY_GAP")):
                    raise Collision("impossible event state transition")
                latest_roles[(stream,role)]=kind
                if parent!=previous or rowhash!=chained(previous,payload):
                    raise Collision("event chain digest altered")
                previous=rowhash
        except (ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,sqlite3.Error) as exc:
            raise Collision("malformed receipt or event journal") from exc
        return {"receipt_count":len(rows),"event_count":len(event_rows),
                "schema":VERSION,"internally_verified":True,
                "indexed_fields_and_event_links_checked":True,
                "external_attestation":False,"production_authorized":False}
