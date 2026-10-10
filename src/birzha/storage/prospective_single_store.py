"""Staging only: immutable Forecast/Outcome plus original evidence in ONE DuckDB transaction.

One file and one connection, not two independent SQLite/DuckDB commits. Existing
canonical table schemas are retained. No production paths, migration, or schedulers.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
from threading import RLock
from typing import Any, Callable

import duckdb

from birzha.application import prospective_capture as pilot
from birzha.application.prospective_staging_bridge import _outcome_from_pilot
from birzha.domain import forecast as fd
from birzha.storage.forecast_journal import DuckDBForecastJournal
from birzha.storage.outcome_journal import DuckDBOutcomeJournal

VERSION = "G2_ATOMIC_SINGLE_DUCKDB_STAGING_V2"
HORIZONS = (5, 10, 20)


def _json(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def _now(clock):
    dt = clock()
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise pilot.AdmissionRefused("timezone-aware staging clock required")
    return dt.astimezone(timezone.utc)


def _validate_record(record, evidence):
    if not isinstance(evidence, pilot.CaptureEvidence) or not hasattr(record, "to_dict"):
        raise pilot.AdmissionRefused("original receipt and ForecastRecord required")
    data = record.to_dict()
    if not isinstance(data, dict) or not all(data.get(k) for k in ("forecast_id", "symbol", "secid", "snapshot_id")):
        raise pilot.AdmissionRefused("incomplete forecast identity")
    ver = data.get("record_version")
    if ver != fd.FORECAST_RECORD_CONTRACT_VERSION:
        permitted = {getattr(fd, "BASELINE_EVIDENCE_RECORD_VERSION", None),
                     getattr(fd, "LEVEL_EVIDENCE_RECORD_VERSION", None)}
        if ver is None or ver not in permitted or not callable(getattr(fd, "validate_baseline_evidence_payload", None)):
            raise pilot.AdmissionRefused("unrecognized forecast evidence version")
        try:
            fd.validate_baseline_evidence_payload(data)
        except (ValueError, TypeError, KeyError, OverflowError) as e:
            raise pilot.AdmissionRefused("malformed V2/V3 forecast evidence") from e
    if not isinstance(evidence.source_payload, bytes) or not evidence.source_payload or evidence.source_origin != "LIVE_CAPTURED_PAYLOAD":
        raise pilot.AdmissionRefused("only original received bytes are admissible")
    if data.get("snapshot_contract_version") != evidence.contract_version:
        raise pilot.AdmissionRefused("snapshot contract mismatch")
    hs = data.get("horizons")
    if not isinstance(hs, list) or len(hs) != 3 or sorted(h.get("sessions") for h in hs) != list(HORIZONS):
        raise pilot.AdmissionRefused("requires exactly 5/10/20")
    if any(h.get("direction") not in ("UP", "DOWN", "NEUTRAL") for h in hs):
        raise pilot.AdmissionRefused("bad direction")
    reference = data.get("reference_price")
    if type(reference) not in (float, int) or not math.isfinite(reference) or reference <= 0:
        raise pilot.AdmissionRefused("invalid causal reference price")
    if not pilot._when(evidence.latest_completed_event_end) <= pilot._when(evidence.source_observed_at) <= pilot._when(data["created_at_t0"]):
        raise pilot.AdmissionRefused("source not observed by forecast T0")
    return data


def _session_times(dates, completed_at, observed_at):
    """Validate caller-supplied D1 session/completion correspondence.

    A D1 session can complete on its Moscow calendar date or overnight on
    the following date. An end many days later, reused completion timestamp,
    or invalid date cannot count as a distinct completed future session.
    This is shape/timing validation, NOT exchange-calendar authentication.
    """
    try:
        days = tuple(date.fromisoformat(item) for item in dates)
        if any(day.isoformat() != raw for day, raw in zip(days, dates)):
            raise ValueError("noncanonical future session date")
    except (TypeError, ValueError) as exc:
        raise pilot.AdmissionRefused("future sessions require canonical ISO dates") from exc
    ends = tuple(pilot._when(stamp) for stamp in completed_at)
    observed = pilot._when(observed_at)
    if any(a >= b for a, b in zip(ends, ends[1:])):
        raise pilot.AdmissionRefused("future session completion times must strictly increase")
    if any(end > observed or not (day <= end.astimezone(pilot.MOEX_TIMEZONE).date() <= day + timedelta(days=1))
           for day, end in zip(days, ends)):
        raise pilot.AdmissionRefused("future completion does not match its stated MOEX session")
    return ends, observed


def _validate_future(data, captured_at, evidence, horizon, now):
    if horizon not in HORIZONS or not isinstance(evidence, pilot.CompletedSessions):
        raise pilot.AdmissionRefused("unknown future horizon")
    if evidence.source_origin != "LIVE_COMPLETED_D1" or evidence.calendar_origin != "VERIFIED_EXCHANGE_SESSION_CALENDAR":
        raise pilot.AdmissionRefused("future source/calendar not verified")
    if not isinstance(evidence.source_payload, bytes) or not evidence.source_payload:
        raise pilot.AdmissionRefused("future raw bytes required")
    arr = (evidence.session_dates, evidence.expected_calendar_dates, evidence.candle_completed_at, evidence.candle_close)
    if any(len(x) != horizon for x in arr):
        raise pilot.AdmissionRefused("missing future sessions")
    # Reject malformed dates before ordering/comparing caller-declared calendars.
    _session_times(evidence.session_dates, evidence.candle_completed_at, evidence.source_observed_at)
    if evidence.session_dates != evidence.expected_calendar_dates or tuple(sorted(set(evidence.session_dates))) != evidence.session_dates:
        raise pilot.AdmissionRefused("invalid calendar/duplicates")
    if (evidence.market, evidence.secid) != (data["symbol"], data["secid"]):
        raise pilot.AdmissionRefused("future exact symbol/SECID mismatch")
    if any(type(c) not in (int, float) or c <= 0 or not math.isfinite(c) for c in evidence.candle_close):
        raise pilot.AdmissionRefused("invalid completed close")
    ends, observed = _session_times(
        evidence.session_dates, evidence.candle_completed_at, evidence.source_observed_at
    )
    if now < observed or observed <= pilot._when(captured_at) or ends[0] <= pilot._when(captured_at):
        raise pilot.AdmissionRefused("outcome source was not truly observed after capture")
    if any(day <= pilot._when(data["created_at_t0"]).date().isoformat() for day in evidence.session_dates):
        raise pilot.AdmissionRefused("future sessions precede forecast")
    direction = next(x["direction"] for x in data["horizons"] if x["sessions"] == horizon)
    close = float(evidence.candle_close[-1])
    ref = float(data["reference_price"])
    ret = (close/ref - 1)*100
    hit = ret>0 if direction=="UP" else ret<0 if direction=="DOWN" else None
    # The canonical outcome needs only the final close. The immutable future
    # evidence additionally commits to *every* supplied candle/time, so altered
    # interior bars are never misclassified as an identical replay.
    supplied = {
        "source_sha256": _sha(evidence.source_payload),
        "source_origin": evidence.source_origin,
        "calendar_origin": evidence.calendar_origin,
        "source_observed_at": pilot._stamp(observed),
        "market": evidence.market, "secid": evidence.secid,
        "session_dates": list(evidence.session_dates),
        "expected_calendar_dates": list(evidence.expected_calendar_dates),
        "candle_completed_at": [pilot._stamp(end) for end in ends],
        "candle_close": [float(value) for value in evidence.candle_close],
    }
    return dict(forecast_id=data["forecast_id"], horizon_sessions=horizon,
        future_input_evidence=supplied,
        future_input_sha256=_sha(_json(supplied).encode("utf-8")),
        market=evidence.market, secid=evidence.secid, status="OBSERVED",
        target_session=evidence.session_dates[-1], target_session_end=pilot._stamp(ends[-1]),
        target_close=close, reference_price=ref, actual_return_pct=round(ret,8),
        direction_hit=hit, future_source_sha256=_sha(evidence.source_payload),
        calendar_sha256=_sha(_json(evidence.expected_calendar_dates).encode()),
        source_observed_at=pilot._stamp(observed), capture_at=captured_at,
        outcome_written_at=pilot._stamp(now), pilot_only=True)


class SingleDuckDBProspectiveStagingJournal:
    """Staging single-process writer, one physical DuckDB file and atomic commits."""

    def __init__(self, path: str | Path, *, staging_root: str | Path,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 fault_hook: Callable[[str], None] | None = None):
        root, file = Path(staging_root).resolve(), Path(path).resolve()
        if not root.is_dir() or str(root)=="/" or not file.is_relative_to(root) or file.suffix!=".duckdb":
            raise pilot.AdmissionRefused("explicit existing disposable .duckdb path required")
        if any(part in str(file).lower().replace("\\", "/") for part in ("mcp-home", "/opt/mcp/", "/production/")):
            raise pilot.AdmissionRefused("no HOME or production path")
        self.path, self.clock, self.fault_hook, self.lock = file,clock,fault_hook,RLock()
        self.db = duckdb.connect(str(file))
        tables={r[0] for r in self.db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}
        if tables and "g2_atomic_meta" not in tables:
            self.db.close()
            raise pilot.AdmissionRefused("refuse unrelated existing DuckDB file")
        self.db.execute("CREATE TABLE IF NOT EXISTS g2_atomic_meta (version VARCHAR PRIMARY KEY)")
        self.db.execute("INSERT OR IGNORE INTO g2_atomic_meta VALUES (?)",[VERSION])
        if self.db.execute("SELECT version FROM g2_atomic_meta").fetchall() != [(VERSION,)]:
            self.db.close()
            raise pilot.AdmissionRefused("incompatible staging version")
        self.db.execute("""CREATE TABLE IF NOT EXISTS forecast_records (
          forecast_id VARCHAR PRIMARY KEY,payload_hash VARCHAR NOT NULL,payload_json VARCHAR NOT NULL,
          symbol VARCHAR NOT NULL,secid VARCHAR NOT NULL,created_at_t0 VARCHAR NOT NULL,
          engine_version VARCHAR NOT NULL,inserted_at TIMESTAMP DEFAULT current_timestamp)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS outcome_records (
          outcome_id VARCHAR PRIMARY KEY,forecast_id VARCHAR NOT NULL,horizon_sessions INTEGER NOT NULL,
          payload_hash VARCHAR NOT NULL,payload_json VARCHAR NOT NULL,
          inserted_at TIMESTAMP DEFAULT current_timestamp)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS g2_atomic_captures (
          forecast_id VARCHAR PRIMARY KEY,forecast_sha VARCHAR NOT NULL,
          source_blob BLOB NOT NULL,source_sha VARCHAR NOT NULL,
          receipt_json VARCHAR NOT NULL,receipt_sha VARCHAR NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS g2_atomic_futures (
          forecast_id VARCHAR NOT NULL,horizon INTEGER NOT NULL,outcome_id VARCHAR NOT NULL,
          source_blob BLOB NOT NULL,source_sha VARCHAR NOT NULL,
          evidence_json VARCHAR NOT NULL,evidence_sha VARCHAR NOT NULL,
          PRIMARY KEY(forecast_id,horizon))""")

    def close(self):
        with self.lock:
            self.db.close()

    def _fault(self, event):
        if self.fault_hook:
            self.fault_hook(event)

    def _tx(self, operation):
        with self.lock:
            self.db.execute("BEGIN TRANSACTION")
            try:
                self._audit()
                result=operation()
                self.db.execute("COMMIT")
                return result
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _audit(self):
        canonical={id:(h,p) for id,h,p in self.db.execute("SELECT forecast_id,payload_hash,payload_json FROM forecast_records").fetchall()}
        sources=self.db.execute("SELECT forecast_id,forecast_sha,source_blob,source_sha,receipt_json,receipt_sha FROM g2_atomic_captures").fetchall()
        if set(canonical)!={r[0] for r in sources}:
            raise pilot.ImmutableCollision("orphan canonical Forecast/source receipt")
        for fid,hash_,raw,sourcehash,receipt,receipthash in sources:
            if _sha(bytes(raw))!=sourcehash or _sha(receipt.encode())!=receipthash or canonical.get(fid)!=(hash_,canonical.get(fid,(None,""))[1]) or _sha(canonical[fid][1].encode())!=hash_:
                raise pilot.ImmutableCollision("corrupt captured Forecast/source bytes")
            meta=json.loads(receipt)
            if (meta.get("forecast_id"),meta.get("forecast_sha256"),meta.get("source_sha256"))!=(fid,hash_,sourcehash):
                raise pilot.ImmutableCollision("source receipt detached from forecast")
        outcome={id:(h,p) for id,h,p in self.db.execute("SELECT outcome_id,payload_hash,payload_json FROM outcome_records").fetchall()}
        future=self.db.execute("SELECT forecast_id,horizon,outcome_id,source_blob,source_sha,evidence_json,evidence_sha FROM g2_atomic_futures").fetchall()
        if set(outcome)!={r[2] for r in future}:
            raise pilot.ImmutableCollision("orphan canonical Outcome/future receipt")
        for fid,horizon,oid,raw,h,receipt,receiptsha in future:
            if fid not in canonical or _sha(bytes(raw))!=h or _sha(receipt.encode())!=receiptsha:
                raise pilot.ImmutableCollision("corrupt future evidence")
            meta=json.loads(receipt)
            if (meta.get("forecast_id"),meta.get("horizon_sessions"),meta.get("future_source_sha256"))!=(fid,horizon,h):
                raise pilot.ImmutableCollision("unbound future evidence")
            # V2 must retain and bind the *full* input candle sequence, not
            # merely the final close projected to the canonical Outcome.
            supplied=meta.get("future_input_evidence")
            if not isinstance(supplied,dict) or _sha(_json(supplied).encode("utf-8"))!=meta.get("future_input_sha256"):
                raise pilot.ImmutableCollision("corrupt future input evidence fingerprint")
            if (supplied.get("source_sha256"), supplied.get("market"), supplied.get("secid"),
                    supplied.get("source_observed_at")) != (h,meta["market"],meta["secid"],meta["source_observed_at"]):
                raise pilot.ImmutableCollision("unbound future input source or instrument")
            dates=supplied.get("session_dates")
            expected=supplied.get("expected_calendar_dates")
            ends=supplied.get("candle_completed_at")
            closes=supplied.get("candle_close")
            if not all(isinstance(items,list) and len(items)==horizon for items in (dates,expected,ends,closes)):
                raise pilot.ImmutableCollision("corrupt future input window")
            if (dates[-1],ends[-1],closes[-1]) != (
                    meta["target_session"],meta["target_session_end"],meta["target_close"]):
                raise pilot.ImmutableCollision("future input contradicts canonical terminal facts")
            if _sha(_json(expected).encode("utf-8"))!=meta["calendar_sha256"]:
                raise pilot.ImmutableCollision("future calendar no longer matches input evidence")
            try:
                _session_times(dates, ends, supplied["source_observed_at"])
            except (pilot.AdmissionRefused, ValueError, TypeError) as exc:
                raise pilot.ImmutableCollision("persisted future session chronology invalid") from exc
            projected=_outcome_from_pilot(meta)
            body,digest=DuckDBOutcomeJournal.canonical_payload(projected)
            if projected.outcome_id!=oid or outcome.get(oid)!=(digest,body):
                raise pilot.ImmutableCollision("canonical Outcome differs from original future")
        return {"captures":len(sources),"outcomes":len(future)}

    def audit(self):
        with self.lock:
            return {"schema":VERSION, **self._audit(), "staging_only":True}

    def capture(self, record, evidence):
        data=_validate_record(record,evidence)
        body,digest=DuckDBForecastJournal.canonical_payload(record)
        sourcehash=_sha(evidence.source_payload)
        def execute():
            fid=data["forecast_id"]
            prev=self.db.execute("SELECT forecast_sha,source_sha,receipt_json FROM g2_atomic_captures WHERE forecast_id=?",[fid]).fetchone()
            if prev is not None:
                if prev[:2]!=(digest,sourcehash):
                    raise pilot.ImmutableCollision("frozen forecast/source collision")
                stored=json.loads(prev[2])
                current_source={
                    "source_origin":evidence.source_origin,
                    "source_observed_at":pilot._stamp(pilot._when(evidence.source_observed_at)),
                    "latest_completed_event_end":pilot._stamp(pilot._when(evidence.latest_completed_event_end)),
                    "snapshot_id":data["snapshot_id"],
                    "secid":data["secid"],
                    "record_version":data["record_version"],
                    "decision_t0":pilot._stamp(pilot._when(data["created_at_t0"])),
                }
                if any(stored.get(key)!=value for key,value in current_source.items()):
                    raise pilot.ImmutableCollision("frozen source evidence metadata differs on replay")
                return {"status":"DUPLICATE_IDENTICAL","receipt":stored}
            now=_now(self.clock)
            obs=pilot._when(evidence.source_observed_at)
            t0=pilot._when(data["created_at_t0"])
            if now<t0 or now<obs or (now-t0).total_seconds()>pilot.MAX_CAPTURE_LAG_SECONDS or (now-obs).total_seconds()>pilot.MAX_CAPTURE_LAG_SECONDS:
                raise pilot.AdmissionRefused("backdated or stale receipt")
            receipt=dict(version=VERSION,forecast_id=fid,forecast_sha256=digest,
                source_sha256=sourcehash,source_origin=evidence.source_origin,
                source_observed_at=pilot._stamp(obs),decision_t0=pilot._stamp(t0),
                latest_completed_event_end=pilot._stamp(pilot._when(evidence.latest_completed_event_end)),
                captured_at=pilot._stamp(now),snapshot_id=data["snapshot_id"],
                secid=data["secid"],record_version=data["record_version"],staging_only=True)
            text=_json(receipt)
            self.db.execute("INSERT INTO g2_atomic_captures VALUES (?,?,?,?,?,?)",
                            [fid,digest,evidence.source_payload,sourcehash,text,_sha(text.encode())])
            self._fault("AFTER_RECEIPT")
            self.db.execute("""INSERT INTO forecast_records
              (forecast_id,payload_hash,payload_json,symbol,secid,created_at_t0,engine_version)
              VALUES (?,?,?,?,?,?,?)""",[fid,digest,body,data["symbol"],data["secid"],data["created_at_t0"],data["engine_version"]])
            self._fault("AFTER_FORECAST")
            return {"status":"APPENDED","receipt":receipt}
        return self._tx(execute)

    def observe(self, forecast_id, horizon, evidence):
        def execute():
            receipt=self.db.execute("SELECT receipt_json FROM g2_atomic_captures WHERE forecast_id=?",[forecast_id]).fetchone()
            frozen=self.db.execute("SELECT payload_json FROM forecast_records WHERE forecast_id=?",[forecast_id]).fetchone()
            if receipt is None or frozen is None:
                raise pilot.AdmissionRefused("no frozen forecast/receipt")
            old=json.loads(receipt[0])
            forecast=json.loads(frozen[0])
            prior=self.db.execute("SELECT source_sha,evidence_json FROM g2_atomic_futures WHERE forecast_id=? AND horizon=?",[forecast_id,horizon]).fetchone()
            calculated=_validate_future(forecast,old["captured_at"],evidence,horizon,_now(self.clock))
            if prior is not None:
                previous=json.loads(prior[1])
                if prior[0]!=_sha(evidence.source_payload) or previous.get("future_input_sha256")!=calculated["future_input_sha256"] or any(previous.get(k)!=calculated[k] for k in
                    ("source_observed_at","calendar_sha256","target_session","target_close","target_session_end","future_source_sha256")):
                    raise pilot.ImmutableCollision("frozen future input evidence differs on replay")
                return {"status":"DUPLICATE_IDENTICAL","outcome":_outcome_from_pilot(previous)}
            item=_outcome_from_pilot(calculated)
            body,digest=DuckDBOutcomeJournal.canonical_payload(item)
            futurejson=_json(calculated)
            self.db.execute("INSERT INTO g2_atomic_futures VALUES (?,?,?,?,?,?,?)",
               [forecast_id,horizon,item.outcome_id,evidence.source_payload,_sha(evidence.source_payload),futurejson,_sha(futurejson.encode())])
            self._fault("AFTER_FUTURE_RECEIPT")
            self.db.execute("""INSERT INTO outcome_records
                (outcome_id,forecast_id,horizon_sessions,payload_hash,payload_json)
                VALUES (?,?,?,?,?)""",[item.outcome_id,forecast_id,horizon,digest,body])
            self._fault("AFTER_OUTCOME")
            return {"status":"APPENDED","outcome":item}
        return self._tx(execute)
