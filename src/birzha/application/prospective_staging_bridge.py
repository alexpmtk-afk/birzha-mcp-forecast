"""Isolated reconciliation bridge from G2 pilot receipts to canonical journals.

Important: two different DBs cannot commit atomically. The pilot receipt is
written first and is durable; canonical writes are idempotent and replayable.
A missing canonical forecast is *not* reconstructed automatically: replay the
original ForecastRecord to close that gap. Entire module is STAGING ONLY.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from birzha.application.prospective_capture import (
    AdmissionRefused, CaptureEvidence, CompletedSessions, ImmutableCollision,
    ProspectivePilotLedger,
)
from birzha.domain.outcome import HorizonOutcome

BRIDGE_VERSION = "G2_CANONICAL_STAGING_BRIDGE_V1"
STAGING_ONLY = "EXPLICIT_STAGING_NO_PRODUCTION"


def _is_within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _canonical_digest(record: Any) -> str:
    text = json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _outcome_from_pilot(data: dict[str, Any]) -> HorizonOutcome:
    """Map only canonical fields; excursion requires high/low (not provided)."""
    forecast_id = str(data["forecast_id"])
    horizon = int(data["horizon_sessions"])
    oid = "out_" + hashlib.sha256(f"{forecast_id}:{horizon}".encode()).hexdigest()[:24]
    return HorizonOutcome(
        outcome_id=oid, forecast_id=forecast_id,
        symbol=str(data["market"]), secid=str(data["secid"]),
        horizon_sessions=horizon,
        reference_price=float(data["reference_price"]),
        target_session_end=str(data["target_session_end"]),
        target_close=float(data["target_close"]),
        actual_return_pct=round(float(data["actual_return_pct"]), 6),
        direction_hit=data["direction_hit"],
        max_favorable_excursion_pct=None, max_adverse_excursion_pct=None,
        status="OBSERVED",
    )


@dataclass(slots=True)
class CanonicalProspectiveStagingBridge:
    """Adapter coordinating isolated pilot evidence and existing DuckDB journals.

    Require all storage paths to be under the same explicit disposable root.
    Not a scheduler, not a real market-data provider, not a production merger.
    """

    pilot: ProspectivePilotLedger
    forecasts: Any  # DuckDBForecastJournal from src/birzha/storage
    outcomes: Any   # DuckDBOutcomeJournal from src/birzha/storage
    staging_root: Path
    mode: str = STAGING_ONLY

    def __post_init__(self) -> None:
        if self.mode != STAGING_ONLY:
            raise AdmissionRefused("production activation forbidden")
        root = Path(self.staging_root).resolve()
        if not root.is_dir() or str(root) in ("/", "."):
            raise AdmissionRefused("explicit existing isolated staging root required")
        lower = str(root).lower().replace("\\", "/")
        if any(fragment in lower for fragment in ("mcp-home", "/opt/mcp/", "/production/")):
            raise AdmissionRefused("production-like staging root forbidden")
        locations = [str(self.pilot.path), str(self.forecasts.path), str(self.outcomes.path)]
        if len(set(locations)) != 3:
            raise AdmissionRefused("require separate isolated sidecar and journal paths")
        if any(location == ":memory:" or not _is_within(Path(location), root) for location in locations):
            raise AdmissionRefused("all storage must be file-backed under isolated staging root")
        self.staging_root = root

    def _check_forecast_binding(self, fid: str, pilot_digest: str) -> Any | None:
        stored = self.forecasts.get(fid)
        if stored is not None and _canonical_digest(stored) != pilot_digest:
            raise ImmutableCollision("canonical Forecast Journal differs from pilot receipt")
        return stored

    def capture(self, record: Any, evidence: CaptureEvidence) -> dict[str, Any]:
        """Receipt first, canonical journal second; identical replay heals crash gap."""
        # Even normal capture/replay needs integrity of prior persisted receipts,
        # not only the special reconcile() recovery path. Staging-only full pass.
        self.pilot.audit()
        fid = str(record.to_dict()["forecast_id"])
        current = self.forecasts.get(fid)
        if current is not None and _canonical_digest(current) != _canonical_digest(record):
            raise ImmutableCollision("conflicting existing Forecast Journal record")
        pilot_receipt = self.pilot.capture(record, evidence)
        # Verify fresh and duplicate receipt hashes before a separate DB write.
        self.pilot.audit()
        receipt = pilot_receipt["receipt"]
        if receipt["forecast_sha256"] != _canonical_digest(record):
            raise ImmutableCollision("pilot/canonical Forecast Record SHA mismatch")
        result = self.forecasts.append(record)
        canonical = self._check_forecast_binding(fid, receipt["forecast_sha256"])
        if canonical is None:
            raise ImmutableCollision("canonical Forecast Journal did not persist the record")
        if result.payload_hash != receipt["forecast_sha256"]:
            raise ImmutableCollision("canonical append SHA not equal to original pilot capture")
        return {"version": BRIDGE_VERSION, "status": result.status,
                "pilot_status": pilot_receipt["status"], "receipt": receipt,
                "canonical_forecast_sha256": result.payload_hash, "staging_only": True}

    def observe(self, forecast_id: str, horizon: int,
                evidence: CompletedSessions) -> dict[str, Any]:
        """Append validated pilot outcome, then canonical HorizonOutcome."""
        # Neither a forged capture receipt nor a forged earlier outcome can be
        # treated as admissible simply because reconcile() was not called.
        self.pilot.audit()
        record = self.pilot.get_capture_record(forecast_id)
        if record is None:
            raise AdmissionRefused("no original prospective receipt")
        if self._check_forecast_binding(forecast_id, record["receipt"]["forecast_sha256"]) is None:
            raise AdmissionRefused("canonical forecast pending; replay original capture first")
        pilot_result = self.pilot.observe(forecast_id, horizon, evidence)
        # Validate the durable pilot sidecar before projecting to DuckDB.
        self.pilot.audit()
        item = _outcome_from_pilot(pilot_result["outcome"])
        self.outcomes.append(item)
        rows = {out.horizon_sessions: out for out in self.outcomes.list_for_forecast(forecast_id)}
        if horizon not in rows or rows[horizon].to_dict() != item.to_dict():
            raise ImmutableCollision("canonical Outcome Journal read-back mismatch")
        return {"version": BRIDGE_VERSION, "status": pilot_result["status"],
                "outcome_id": item.outcome_id,
                "canonical_outcome_sha256": hashlib.sha256(json.dumps(
                    item.to_dict(), sort_keys=True, ensure_ascii=False,
                    separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                "staging_only": True}

    def reconcile(self) -> dict[str, Any]:
        """Repair missing canonical outcomes, report missing forecasts as pending.

        This should run only on isolated staging. It never manufactures first
        receipt, re-dates a prediction or reads market data. Pilot hashes are
        audited before any writes. Canonical-only outcomes are a hard error.
        """
        audit = self.pilot.audit()
        pending_forecasts = []
        replayed_outcomes = []
        for fid in audit["forecast_ids"]:
            record = self.pilot.get_capture_record(fid)
            if record is None:
                raise ImmutableCollision("pilot audit/record inventory divergence")
            canonical = self._check_forecast_binding(fid, record["receipt"]["forecast_sha256"])
            if canonical is None:
                pending_forecasts.append(fid)
                continue
            source = {x["horizon_sessions"]: x for x in self.pilot.list_outcomes(fid)}
            existing = {x.horizon_sessions: x for x in self.outcomes.list_for_forecast(fid)}
            if set(existing) - set(source):
                raise ImmutableCollision("canonical Outcome Journal has unreceipted outcome")
            for horizon, payload in source.items():
                projected = _outcome_from_pilot(payload)
                if horizon in existing and existing[horizon].to_dict() != projected.to_dict():
                    raise ImmutableCollision("canonical Outcome Journal conflicts with pilot evidence")
                if horizon not in existing:
                    self.outcomes.append(projected)
                    replayed_outcomes.append(projected.outcome_id)
        return {"version": BRIDGE_VERSION, "pilot_captures": audit["captures"],
                "pilot_outcomes": audit["outcomes"],
                "pending_forecast_replay_ids": pending_forecasts,
                "recovered_outcome_ids": replayed_outcomes,
                "status": "PENDING_FORECAST_REPLAY" if pending_forecasts else "CONSISTENT",
                "staging_only": True}
