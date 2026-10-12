"""Q-090: isolated first *local intake* of original M1 response bytes.

Call this directly when the response bytes enter a staging-only collector.
It is NOT proof of actual network arrival, TLS origin, independent UTC clock,
external RFC3161 timestamp, completed session or production authorization.
Never backfill first receipts from historical source files.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Callable

from birzha.application.g2_m1_m15_source_replay import (
    OriginalM1Page, _identity as exact_instrument, _utc as claimed_time,
)
from birzha.application.g2_m15_staging_preflight import (
    SessionNoticeCandidate, prepare_m15_staging_preflight,
)
from birzha.domain.market import Candle, Instrument

VERSION = "G2_FIRST_LOCAL_M1_INTAKE_V1"
HANDOFF = "G2_FIRST_LOCAL_M15_HANDOFF_V1"
KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
MAX_SOURCE_BYTES = 1_500_000
MAX_RECEIPT_DELAY = 120


class LocalIntakeRefused(ValueError):
    """Original page is not admissible at this local intake event."""


class LocalIntakeCollision(RuntimeError):
    """An existing first-receipt identity or immutable source was changed."""


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LocalIntakeRefused("local clock needs offset-aware datetime")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00","Z")


def _canon(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      allow_nan=False, separators=(",", ":"))


def _hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _chain(parent: str, payload: str) -> str:
    return _hash((parent+"\n"+payload).encode("utf-8"))


def _key(key: str) -> str:
    if not isinstance(key,str) or not KEY.fullmatch(key):
        raise LocalIntakeRefused("bounded explicit capture identifier required")
    return key


class OriginalM1LocalIntake:
    """Append-only, crash-safe SQLite receipts under an explicit scratch root.

    The trusted part of a record is only what THIS process locally committed.
    Caller-supplied page/request/response metadata is retained *as claimed*.
    """
    def __init__(self, path: str | Path, *, staging_root: str | Path,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        root=Path(staging_root)
        dest=Path(path)
        if (not root.is_dir() or root.is_symlink() or dest.is_symlink()
            or dest.parent.resolve()!=root.resolve()
            or not dest.name.startswith("g2_m1_receipts_")
            or dest.suffix!=".sqlite3"):
            raise LocalIntakeRefused("explicit existing isolated staging root/path required")
        self.path=dest
        self.clock=clock
        self.db=sqlite3.connect(str(dest),isolation_level=None,timeout=5)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA foreign_keys=ON")
        before={v[0] for v in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        triggers={v[0] for v in self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        protected={"m1_local_no_update","m1_local_no_delete"}
        if before and (before!={"m1_local_meta","m1_local_receipts"} or not protected.issubset(triggers)):
            self.db.close()
            raise LocalIntakeRefused("foreign, incomplete, or weakened existing intake journal")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS m1_local_meta (version TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS m1_local_receipts (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              capture_key TEXT NOT NULL, stream TEXT NOT NULL,
              page_start INTEGER NOT NULL, local_received_utc TEXT NOT NULL,
              envelope TEXT NOT NULL, raw BLOB NOT NULL,
              raw_sha256 TEXT NOT NULL, parent_sha256 TEXT NOT NULL,
              receipt_sha256 TEXT NOT NULL,
              UNIQUE(capture_key,stream,page_start)
            );
            CREATE TRIGGER IF NOT EXISTS m1_local_no_update
              BEFORE UPDATE ON m1_local_receipts
              BEGIN SELECT RAISE(ABORT,'append-only M1 receipt'); END;
            CREATE TRIGGER IF NOT EXISTS m1_local_no_delete
              BEFORE DELETE ON m1_local_receipts
              BEGIN SELECT RAISE(ABORT,'append-only M1 receipt'); END;
        """)
        self.db.execute("INSERT OR IGNORE INTO m1_local_meta VALUES (?)",(VERSION,))
        if self.db.execute("SELECT version FROM m1_local_meta").fetchall()!=[(VERSION,)]:
            self.db.close()
            raise LocalIntakeRefused("incorrect intake journal version")
        self.audit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self,*_args):
        self.close()

    def append(self, *, capture_key: str, instrument: Instrument,
               page: OriginalM1Page) -> dict:
        # Read clock immediately on entry rather than trusting caller's
        # response_received_utc. This remains a LOCAL, not independent clock.
        now=_utc(self.clock())
        received=_stamp(now)
        key=_key(capture_key)
        try:
            stream=exact_instrument(instrument)
        except (TypeError,ValueError) as exc:
            raise LocalIntakeRefused("invalid exact MOEX identity") from exc
        if not isinstance(page,OriginalM1Page):
            raise TypeError("OriginalM1Page required")
        if (not isinstance(page.raw_body,bytes)
            or not 0<len(page.raw_body)<=MAX_SOURCE_BYTES
            or not isinstance(page.raw_sha256,str)
            or page.raw_sha256!=_hash(page.raw_body)
            or type(page.start) is not int or page.start<0
            or type(page.http_status) is not int
            or not isinstance(page.request_url,str)
            or not isinstance(page.content_type,str)):
            raise LocalIntakeRefused("malformed or changed original page")
        try:
            request=claimed_time(page.request_started_utc)
            reported=claimed_time(page.response_received_utc)
        except ValueError as exc:
            raise LocalIntakeRefused("invalid caller-reported response times") from exc
        if not request<=reported<=now:
            raise LocalIntakeRefused("noncausal caller-reported page receipt")
        fields={
            "request_url_claim":page.request_url,
            "http_status_claim":page.http_status,
            "content_type_claim":page.content_type,
            "request_started_utc_claim":page.request_started_utc,
            "response_received_utc_claim":page.response_received_utc,
            "page_start":page.start,
            "raw_sha256":page.raw_sha256,
        }
        with_envelope=_canon(fields)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            existing=self.db.execute(
                "SELECT seq,envelope,local_received_utc,raw_sha256 "
                "FROM m1_local_receipts WHERE capture_key=? AND stream=? AND page_start=?",
                (key,stream,page.start)).fetchone()
            if existing:
                saved=json.loads(existing[1])["original_page"]
                if _canon(saved)!=with_envelope or existing[3]!=page.raw_sha256:
                    raise LocalIntakeCollision("conflicting original bytes/page metadata")
                self.db.execute("COMMIT")
                return {"status":"DUPLICATE_IDENTICAL","receipt_seq":existing[0],
                        "first_local_received_utc":existing[2],"original_bytes_sha256":existing[3],
                        "new_receipts":0,"independently_attested":False}
            # Only NEW source pages need the recency gate. Caller cannot
            # introduce historical pages with forged new capture identifiers.
            if (now-reported).total_seconds()>MAX_RECEIPT_DELAY:
                raise LocalIntakeRefused("source backfill is not first local intake")
            previous_capture=self.db.execute(
                "SELECT stream,page_start,envelope FROM m1_local_receipts "
                "WHERE capture_key=? ORDER BY seq DESC LIMIT 1",(key,)).fetchone()
            if previous_capture:
                if previous_capture[0]!=stream or page.start<=previous_capture[1]:
                    raise LocalIntakeRefused("capture stream changed or page reversed")
            elif page.start!=0:
                raise LocalIntakeRefused("first original page must begin at offset zero")
            last=self.db.execute(
                "SELECT receipt_sha256,local_received_utc FROM m1_local_receipts "
                "ORDER BY seq DESC LIMIT 1").fetchone()
            if last and received<last[1]:
                raise LocalIntakeRefused("local first-receipt clock moved backwards")
            parent=last[0] if last else "GENESIS"
            envelope=_canon({"schema":VERSION,"capture_key":key,
                             "stream":stream,"local_first_received_utc":received,
                             "original_page":fields})
            receipt_hash=_chain(parent,envelope)
            cur=self.db.execute(
                "INSERT INTO m1_local_receipts "
                "(capture_key,stream,page_start,local_received_utc,envelope,raw,"
                "raw_sha256,parent_sha256,receipt_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                (key,stream,page.start,received,envelope,page.raw_body,
                 page.raw_sha256,parent,receipt_hash))
            self.db.execute("COMMIT")
            return {"status":"NEW_LOCAL_RECEIPT","receipt_seq":cur.lastrowid,
                    "first_local_received_utc":received,
                    "original_bytes_sha256":page.raw_sha256,
                    "new_receipts":1,"independently_attested":False}
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def audit(self) -> dict:
        receipts=self.db.execute(
            "SELECT seq,capture_key,stream,page_start,local_received_utc,"
            "envelope,raw,raw_sha256,parent_sha256,receipt_sha256 "
            "FROM m1_local_receipts ORDER BY seq").fetchall()
        parent="GENESIS"
        last_clock=None
        capture_last={}
        for seq,key,stream,start,received,envelope,raw,sha,prev,rowhash in receipts:
            try:
                obj=json.loads(envelope)
                original=obj["original_page"]
                if (envelope!=_canon(obj) or obj["schema"]!=VERSION
                    or obj["capture_key"]!=key or obj["stream"]!=stream
                    or obj["local_first_received_utc"]!=received
                    or type(start) is not int or start<0
                    or original["page_start"]!=start
                    or original["raw_sha256"]!=sha
                    or not isinstance(raw,bytes) or sha!=_hash(raw)
                    or not isinstance(json.loads(_canon(obj)),dict)
                    or _stamp(claimed_time(received))!=received
                    or (last_clock is not None and received<last_clock)
                    or prev!=parent or rowhash!=_chain(parent,envelope)):
                    raise LocalIntakeCollision("local intake metadata or hash chain altered")
                previous=capture_last.get(key)
                if previous:
                    if previous[0]!=stream or start<=previous[1]:
                        raise LocalIntakeCollision("capture stream sequence altered")
                elif start!=0:
                    raise LocalIntakeCollision("initial page offset altered")
                capture_last[key]=(stream,start)
                last_clock=received
                parent=rowhash
            except (ValueError,TypeError,KeyError,OverflowError) as exc:
                raise LocalIntakeCollision("unverifiable local intake row") from exc
        return {"schema":VERSION,"receipt_count":len(receipts),
                "internally_hash_audited":True,
                "independent_original_network_receipt":False,
                "independent_clock_or_tsa":False,"production_authorized":False}

    def pages(self, *, capture_key: str, instrument: Instrument) -> tuple[OriginalM1Page,...]:
        self.audit()
        key=_key(capture_key)
        stream=exact_instrument(instrument)
        records=self.db.execute(
            "SELECT envelope,raw FROM m1_local_receipts "
            "WHERE capture_key=? AND stream=? ORDER BY seq",
            (key,stream)).fetchall()
        if not records:
            raise LocalIntakeRefused("requested original capture absent")
        values=[]
        for text,raw in records:
            p=json.loads(text)["original_page"]
            values.append(OriginalM1Page(
                raw_body=raw,raw_sha256=p["raw_sha256"],
                request_url=p["request_url_claim"],
                http_status=p["http_status_claim"],content_type=p["content_type_claim"],
                request_started_utc=p["request_started_utc_claim"],
                response_received_utc=p["response_received_utc_claim"],
                start=p["page_start"]))
        return tuple(values)

    def original_capture_handoff(
        self, *, capture_key: str, instrument: Instrument, candidate: Candle,
        session_notice: SessionNoticeCandidate, evaluated_at: datetime,
    ) -> dict:
        """Read-only SHA-bound source->M15 preflight from stored original pages.

        No RFC3161 token is obtained, no exact source status granted, and
        no LevelEventPilot database, forecast, or production write occurs.
        """
        self.audit()
        pages=self.pages(capture_key=capture_key,instrument=instrument)
        when=_utc(evaluated_at)
        record=self.db.execute(
            "SELECT seq,local_received_utc,raw_sha256,envelope FROM m1_local_receipts "
            "WHERE capture_key=? ORDER BY seq",(_key(capture_key),)).fetchall()
        # Require every page to be locally committed by the claimed T0.
        # Checking just the first receipt let subsequent pages enter
        # a historical preflight before their original local intake.
        if not record or any(claimed_time(row[1]) > when for row in record):
            raise LocalIntakeRefused("M15 evaluation predates a locally received page")
        from birzha.application.g2_m15_staging_preflight import DryRunRefused
        try:
            proof=prepare_m15_staging_preflight(
                instrument=instrument,pages=pages,candidate=candidate,
                session_notice=session_notice,clock=lambda:when)
        except DryRunRefused as exc:
            raise LocalIntakeRefused("source-to-M15 offline replay failed") from exc
        handoff={
            "schema":HANDOFF,"capture_key":_key(capture_key),
            "exact_instrument":exact_instrument(instrument),
            "q088_original_commitment_sha256":proof["commitment_sha256"],
            "local_first_received_at_utc":record[0][1],
            "original_receipts":[
                {"seq":seq,"local_received_utc":received,"raw_sha256":sha,
                 "intake_envelope_sha256":_hash(env.encode("utf8"))}
                for seq,received,sha,env in record
            ],
            "first_actual_upstream_delivery_independently_proven":False,
            "external_rfc3161_on_this_handoff":False,
            "moex_transport_independently_attested":False,
            "official_session_calendar_independently_verified":False,
            "may_promote_to_exact_or_append_level_journal":False,
            "forecast_or_trade_authorized":False,
            "production_authorized":False,
        }
        encoded=(_canon(handoff)+"\n").encode("utf8")
        return {"status":"LOCAL_INTAKE_AND_M15_CONTENT_BOUND_EXTERNAL_GATE_BLOCKED",
                "schema":HANDOFF,"manifest":handoff,
                "handoff_bytes":encoded,"handoff_sha256":_hash(encoded),
                "q088":proof,"new_writes":0,
                "level_journal_admission_authorized":False,
                "production_authorized":False}
