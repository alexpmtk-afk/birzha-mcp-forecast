"""Isolated prospective capture/reconciliation pilot, never a production writer.

No network, market-data client, forecast engine, scheduler or DuckDB connection.
Admission requires a recent captured input payload and a ForecastRecord.to_dict()
compatible snapshot; later outcomes require future completed exact-SECID sessions.
The SQLite pilot is deliberately separate from the canonical Forecast/Outcome Journals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable
from zoneinfo import ZoneInfo

VERSION = "G2_PROSPECTIVE_CAPTURE_PILOT_V1"
HORIZONS = (5, 10, 20)
MAX_CAPTURE_LAG_SECONDS = 300
MOEX_TIMEZONE = ZoneInfo("Europe/Moscow")


class AdmissionRefused(ValueError):
    """Capture / outcome evidence violates the prospective contract."""


class ImmutableCollision(RuntimeError):
    """One immutable identity has been reused for conflicting content."""


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _when(stamp: str) -> datetime:
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise AdmissionRefused("timestamps must be ISO8601 with explicit timezone") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise AdmissionRefused("timestamp without explicit timezone")
    return dt.astimezone(timezone.utc)


def _stamp(dt: datetime) -> str:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise AdmissionRefused("clock must be timezone-aware")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clock() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CaptureEvidence:
    """Supply an actually captured source snapshot, not historical reconstruction."""

    source_payload: bytes
    source_observed_at: str
    latest_completed_event_end: str
    source_origin: str = "LIVE_CAPTURED_PAYLOAD"
    contract_version: str = "MARKET_SNAPSHOT_V2"


@dataclass(frozen=True)
class CompletedSessions:
    """An independently acquired future calendar and D1 candle payload.

    The caller remains responsible for authenticating the exchange/calendar
    provider. This guard checks shape, timing, contract and completeness only.
    """

    source_payload: bytes
    source_observed_at: str
    market: str
    secid: str
    session_dates: tuple[str, ...]
    expected_calendar_dates: tuple[str, ...]
    candle_completed_at: tuple[str, ...]
    candle_close: tuple[float, ...]
    source_origin: str = "LIVE_COMPLETED_D1"
    calendar_origin: str = "VERIFIED_EXCHANGE_SESSION_CALENDAR"


class ProspectivePilotLedger:
    """Atomic append-only staging receipts and subsequent outcomes.

    No production integration or implicit HOME paths. Users must explicitly
    supply a new non-production path. Existing files are opened only when they
    carry this schema (fail closed on unrelated SQLite files).
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] = _clock):
        p = Path(path)
        if str(p) == ":memory:" or p.suffix != ".sqlite3":
            raise AdmissionRefused("use an explicit isolated .sqlite3 file path")
        self.path = p
        self._clock = clock
        self._lock = threading.RLock()
        p.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(p), isolation_level=None)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=4000")
        tables = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and "pilot_meta" not in tables:
            self._db.close()
            raise AdmissionRefused("target already has unrelated tables")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS pilot_meta (version TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS captures (
                forecast_id TEXT PRIMARY KEY, forecast_json TEXT NOT NULL,
                forecast_sha TEXT NOT NULL, input_blob BLOB NOT NULL,
                input_sha TEXT NOT NULL, receipt_json TEXT NOT NULL,
                receipt_sha TEXT NOT NULL, captured_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outcomes (
                forecast_id TEXT NOT NULL, horizon INTEGER NOT NULL,
                payload_json TEXT NOT NULL, payload_sha TEXT NOT NULL,
                input_blob BLOB NOT NULL, input_sha TEXT NOT NULL,
                PRIMARY KEY(forecast_id,horizon),
                FOREIGN KEY(forecast_id) REFERENCES captures(forecast_id)
            );
            CREATE TRIGGER IF NOT EXISTS captures_no_update BEFORE UPDATE ON captures
              BEGIN SELECT RAISE(ABORT, 'append-only capture'); END;
            CREATE TRIGGER IF NOT EXISTS captures_no_delete BEFORE DELETE ON captures
              BEGIN SELECT RAISE(ABORT, 'append-only capture'); END;
            CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
              BEGIN SELECT RAISE(ABORT, 'append-only outcome'); END;
            CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
              BEGIN SELECT RAISE(ABORT, 'append-only outcome'); END;
        """)
        self._db.execute("INSERT OR IGNORE INTO pilot_meta(version) VALUES (?)", (VERSION,))
        versions = [x[0] for x in self._db.execute("SELECT version FROM pilot_meta")]
        if versions != [VERSION]:
            self._db.close()
            raise AdmissionRefused("incompatible pilot schema")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def capture(self, record: Any, evidence: CaptureEvidence) -> dict[str, Any]:
        """Commit immutable ForecastRecord payload+source bytes+receipt atomically.

        Identical retries return the original capture time, not a new time.
        Future outcome data is never accepted by this method.
        """
        data = record.to_dict()
        if not isinstance(data, dict) or not data.get("forecast_id"):
            raise AdmissionRefused("requires a versioned ForecastRecord.to_dict()")
        if not data.get("symbol") or not data.get("secid"):
            raise AdmissionRefused("missing exact symbol/SECID")
        if data.get("record_version") != "FORECAST_RECORD_V1_PROTOCOL_08":
            raise AdmissionRefused("unrecognized Forecast Record contract")
        if not data.get("snapshot_id") or data.get("snapshot_contract_version") != evidence.contract_version:
            raise AdmissionRefused("snapshot identity/contract must be anchored")
        horizons = data.get("horizons")
        if not isinstance(horizons, list) or sorted(h["sessions"] for h in horizons) != list(HORIZONS):
            raise AdmissionRefused("requires exactly 5/10/20 session horizons")
        if any(h.get("direction") not in ("UP", "DOWN", "NEUTRAL") for h in horizons):
            raise AdmissionRefused("unknown forecast direction")
        reference = data.get("reference_price")
        if not isinstance(reference, (int, float)) or not math.isfinite(reference) or reference <= 0:
            raise AdmissionRefused("positive finite causal reference price required")
        if evidence.source_origin != "LIVE_CAPTURED_PAYLOAD":
            raise AdmissionRefused("reconstructed or fabricated source is inadmissible")
        if not isinstance(evidence.source_payload, bytes) or not evidence.source_payload:
            raise AdmissionRefused("original input payload bytes are mandatory")
        t0 = _when(data["created_at_t0"])
        observed = _when(evidence.source_observed_at)
        completed = _when(evidence.latest_completed_event_end)
        if completed > observed or observed > t0:
            raise AdmissionRefused("future data or unobserved source at forecast T0")
        encoded = _json(data)
        encoded_sha = _sha(encoded.encode("utf8"))
        input_sha = _sha(evidence.source_payload)
        fid = str(data["forecast_id"])
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                old = self._db.execute("SELECT forecast_sha,input_sha,receipt_json FROM captures WHERE forecast_id=?", (fid,)).fetchone()
                if old:
                    if old[0] != encoded_sha or old[1] != input_sha:
                        raise ImmutableCollision("same forecast_id with altered forecast or input evidence")
                    receipt = json.loads(old[2])
                    self._db.execute("COMMIT")
                    return {"status":"DUPLICATE_IDENTICAL", "receipt":receipt}
                now = _when(_stamp(self._clock()))
                if now < t0 or now < observed:
                    raise AdmissionRefused("capture cannot predate prediction/source")
                if (now-t0).total_seconds() > MAX_CAPTURE_LAG_SECONDS or (now-observed).total_seconds() > MAX_CAPTURE_LAG_SECONDS:
                    raise AdmissionRefused("stale or retrospective source/prediction")
                receipt = {
                    "version":VERSION, "forecast_id":fid, "symbol":data["symbol"], "secid":data["secid"],
                    "forecast_sha256":encoded_sha, "source_sha256":input_sha,
                    "source_origin":evidence.source_origin, "source_observed_at":_stamp(observed),
                    "latest_completed_event_end":_stamp(completed), "decision_t0":_stamp(t0),
                    "captured_at":_stamp(now), "snapshot_id":data["snapshot_id"],
                    "record_version":data["record_version"], "schema":evidence.contract_version,
                    "pilot_only":True,"strict_historical_pit":False,
                }
                receipt_json = _json(receipt)
                self._db.execute("INSERT INTO captures VALUES (?,?,?,?,?,?,?,?)", (
                    fid, encoded,encoded_sha,evidence.source_payload,input_sha,
                    receipt_json,_sha(receipt_json.encode("utf8")),receipt["captured_at"],
                ))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return {"status":"APPENDED","receipt":receipt}

    def observe(self, forecast_id: str, horizon: int, evidence: CompletedSessions) -> dict[str, Any]:
        """Append one validated future horizon, never update the prediction."""
        if horizon not in HORIZONS:
            raise AdmissionRefused("unrecognized horizon")
        if evidence.source_origin != "LIVE_COMPLETED_D1" or evidence.calendar_origin != "VERIFIED_EXCHANGE_SESSION_CALENDAR":
            raise AdmissionRefused("unverified source/calendar")
        if not isinstance(evidence.source_payload, bytes) or not evidence.source_payload:
            raise AdmissionRefused("must preserve original future source bytes")
        arrays=(evidence.session_dates,evidence.expected_calendar_dates,evidence.candle_completed_at,evidence.candle_close)
        if any(len(x)!=horizon for x in arrays):
            raise AdmissionRefused("exact horizon/calendar/close counts required")
        if evidence.session_dates!=evidence.expected_calendar_dates or tuple(sorted(set(evidence.session_dates)))!=evidence.session_dates:
            raise AdmissionRefused("missing, extra or out-of-order sessions")
        if any(not isinstance(x,(float,int)) or not math.isfinite(x) or x<=0 for x in evidence.candle_close):
            raise AdmissionRefused("invalid future completed close")
        observed = _when(evidence.source_observed_at)
        completed_times=tuple(_when(x) for x in evidence.candle_completed_at)
        for day,completed in zip(evidence.session_dates,completed_times):
            if completed.astimezone(MOEX_TIMEZONE).date().isoformat() < day or completed>observed:
                raise AdmissionRefused("uncompleted or not-yet-observed candle")
        if tuple(sorted(completed_times))!=completed_times:
            raise AdmissionRefused("completion timestamps not ordered")
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row=self._db.execute("SELECT forecast_json,receipt_json FROM captures WHERE forecast_id=?",(forecast_id,)).fetchone()
                if row is None:
                    raise AdmissionRefused("forecast not previously captured")
                forecast=json.loads(row[0]); receipt=json.loads(row[1])
                if forecast["secid"]!=evidence.secid or forecast["symbol"]!=evidence.market:
                    raise AdmissionRefused("exact SECID/market mismatch")
                capture_at=_when(receipt["captured_at"])
                if observed<=capture_at or completed_times[0]<=capture_at:
                    raise AdmissionRefused("outcome must mature AFTER frozen capture")
                if any(day<=_when(forecast["created_at_t0"]).date().isoformat() for day in evidence.session_dates):
                    raise AdmissionRefused("outcome session not after forecast T0")
                now=_when(_stamp(self._clock()))
                if now<observed:
                    raise AdmissionRefused("outcome observation cannot come from the future")
                pred=next(h["direction"] for h in forecast["horizons"] if h["sessions"]==horizon)
                ref=float(forecast["reference_price"])
                target=float(evidence.candle_close[-1]); ret=(target/ref-1)*100
                hit=(ret>0 if pred=="UP" else ret<0 if pred=="DOWN" else None)
                result={
                    "version":VERSION,"forecast_id":forecast_id,"horizon_sessions":horizon,
                    "market":evidence.market,"secid":evidence.secid,"status":"OBSERVED",
                    "target_session":evidence.session_dates[-1],"target_session_end":_stamp(completed_times[-1]),
                    "target_close":target,"reference_price":ref,"actual_return_pct":round(ret,8),
                    "direction_hit":hit,"future_source_sha256":_sha(evidence.source_payload),
                    "calendar_sha256":_sha(_json(evidence.expected_calendar_dates).encode("utf8")),
                    "source_observed_at":_stamp(observed),"capture_at":receipt["captured_at"],
                    "outcome_written_at":_stamp(now),"pilot_only":True,
                }
                old=self._db.execute("SELECT payload_json,input_sha FROM outcomes WHERE forecast_id=? AND horizon=?",(forecast_id,horizon)).fetchone()
                if old:
                    previous=json.loads(old[0])
                    if old[1]!=result["future_source_sha256"] or any(previous.get(k)!=result[k] for k in (
                        "target_session","target_close","actual_return_pct","target_session_end","calendar_sha256","source_observed_at"
                    )):
                        raise ImmutableCollision("same horizon identity but changed source/price/calendar")
                    self._db.execute("COMMIT")
                    return {"status":"DUPLICATE_IDENTICAL","outcome":previous}
                data=_json(result)
                self._db.execute("INSERT INTO outcomes VALUES (?,?,?,?,?,?)",(
                    forecast_id,horizon,data,_sha(data.encode("utf8")),evidence.source_payload,
                    result["future_source_sha256"]
                ))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return {"status":"APPENDED","outcome":result}

    def get_capture_record(self, forecast_id: str) -> dict[str, Any] | None:
        """Return immutable metadata, never original input bytes or editable state."""
        with self._lock:
            row = self._db.execute(
                "SELECT forecast_json,receipt_json FROM captures WHERE forecast_id=?",
                (forecast_id,),
            ).fetchone()
        if row is None:
            return None
        return {"forecast": json.loads(row[0]), "receipt": json.loads(row[1])}

    def list_outcomes(self, forecast_id: str) -> list[dict[str, Any]]:
        """Read-only pilot evidence for crash-safe canonical replay."""
        with self._lock:
            rows = self._db.execute(
                "SELECT payload_json FROM outcomes WHERE forecast_id=? ORDER BY horizon",
                (forecast_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def audit(self) -> dict[str, Any]:
        """Recalculate stored hashes; this is corruption detection, NOT external anchoring."""
        with self._lock:
            forecasts=self._db.execute("SELECT forecast_id,forecast_json,forecast_sha,input_blob,input_sha,receipt_json,receipt_sha FROM captures ORDER BY forecast_id").fetchall()
            outcomes=self._db.execute("SELECT forecast_id,horizon,payload_json,payload_sha,input_blob,input_sha FROM outcomes ORDER BY forecast_id,horizon").fetchall()
        for fid,body,digest,src,src_digest,receipt,r_digest in forecasts:
            if _sha(body.encode("utf8"))!=digest or _sha(src)!=src_digest or _sha(receipt.encode("utf8"))!=r_digest:
                raise ImmutableCollision(f"corrupt capture row: {fid}")
            content=json.loads(receipt)
            if content["forecast_sha256"]!=digest or content["source_sha256"]!=src_digest:
                raise ImmutableCollision(f"unbound receipt: {fid}")
        for fid,h,body,digest,src,src_digest in outcomes:
            if _sha(body.encode("utf8"))!=digest or _sha(src)!=src_digest or json.loads(body)["future_source_sha256"]!=src_digest:
                raise ImmutableCollision(f"corrupt outcome row: {fid}/{h}")
        return {"version":VERSION,"captures":len(forecasts),"outcomes":len(outcomes),
            "forecast_ids":[x[0] for x in forecasts],"pilot_only":True,
            "note":"local hash integrity does not prove external timestamp or source authenticity"}
