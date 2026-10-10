"""Strict source-receipt-bound first issuance of an UNVALIDATED baseline.

This module is a *pre-capture admission gate*, not a replacement data provider,
production ForecastService, authentication system or outcome writer. Call only
after independently reconstructing/verifying original market source pages.
The normal MarketSnapshotService event-time path does not satisfy this gate.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import math
from typing import Callable

from birzha.application.forecast import (
    DEFAULT_FORECAST_PARAMETERS,
    ForecastParameters,
    build_forecast_from_snapshot,
)
from birzha.application.prospective_capture import (
    AdmissionRefused, CaptureEvidence, MAX_CAPTURE_LAG_SECONDS,
)
from birzha.domain.forecast import ForecastRecord
from birzha.domain.snapshot import MarketSnapshot, market_snapshot_id


LIVE_SOURCE = "MOEX_ISS_GOVERNED_OBSERVED_INPUT"
REQUIRED_TIMEFRAMES = ("D1", "H1", "M15")


@dataclass(frozen=True, slots=True)
class PreparedSourceBoundForecast:
    """Prepared immutable values. No journal/network writes have occurred."""
    snapshot: MarketSnapshot
    record: ForecastRecord
    evidence: CaptureEvidence


def _when(value: str) -> datetime:
    if not isinstance(value, str):
        raise AdmissionRefused("issuance timestamp must be a string with UTC offset")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdmissionRefused("invalid ISO8601 issuance/source timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise AdmissionRefused("source and issuance clocks must have explicit timezone")
    return result.astimezone(timezone.utc)


def _now_utc(clock: Callable[[], datetime]) -> datetime:
    moment = clock()
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise AdmissionRefused("issuance clock must be timezone-aware")
    return moment.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def prepare_source_bound_forecast(
    snapshot: MarketSnapshot,
    evidence: CaptureEvidence,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    parameters: ForecastParameters = DEFAULT_FORECAST_PARAMETERS,
) -> PreparedSourceBoundForecast:
    """Assign a fresh factual issue T0, never re-use a candle event timestamp.

    Expects a replay-verified MarketSnapshot from the G2 governed RAW assembler,
    with exact raw source SHA and original observed/candle-end receipts.
    A warning marker alone is NOT provider authentication or feature replay.
    """
    if not isinstance(snapshot, MarketSnapshot) or not isinstance(evidence, CaptureEvidence):
        raise AdmissionRefused("source-bound issuance requires snapshot and original receipt")
    if snapshot.source != LIVE_SOURCE or evidence.source_origin != "LIVE_CAPTURED_PAYLOAD":
        raise AdmissionRefused("legacy/reconstructed source cannot be prospective")
    if evidence.contract_version != snapshot.contract_version:
        raise AdmissionRefused("market source/snapshot version mismatch")
    if not isinstance(evidence.source_payload, bytes) or not evidence.source_payload:
        raise AdmissionRefused("missing original serialized provider source bytes")
    if not snapshot.secid or not snapshot.symbol or snapshot.secid.strip() != snapshot.secid:
        raise AdmissionRefused("exact instrument SECID required")
    if snapshot.flow is not None or snapshot.volume_profile is not None:
        # Price-only G2 snapshot lacks separately receipted flow/profile inputs.
        raise AdmissionRefused("unreceipted flow or volume-profile input is forbidden")
    if snapshot.quality_contract is None or snapshot.quality_contract.status != "DEGRADED":
        raise AdmissionRefused("expected explicitly degraded uncalibrated source quality")
    if snapshot.data_quality != "DEGRADED":
        raise AdmissionRefused("quality status differs from source-limited contract")
    if snapshot.quality_contract.flow_status != "NOT_REQUESTED":
        raise AdmissionRefused("source evidence does not cover independent flow provenance")
    quality = snapshot.quality_contract.timeframes
    if len(quality) != len(REQUIRED_TIMEFRAMES) or {
        tf.timeframe for tf in quality
    } != set(REQUIRED_TIMEFRAMES):
        raise AdmissionRefused("must verify all exact D1/H1/M15 windows")
    for tf in quality:
        if tf.status != "PASS" or tf.candles < 50 or not tf.latest_completed_end:
            raise AdmissionRefused("insufficient completed verified source timeframe")
        current = getattr(snapshot, tf.timeframe.lower())
        if current.candles != tf.candles or current.timeframe != tf.timeframe:
            raise AdmissionRefused("snapshot timeframe count inconsistent with source")
        if current.last_close is None or not math.isfinite(current.last_close) or current.last_close <= 0:
            raise AdmissionRefused("source has no finite positive last close")
    source_sha = sha256(evidence.source_payload).hexdigest()
    marker = "source_raw_bundle_sha256=" + source_sha
    if snapshot.warnings.count(marker) != 1:
        raise AdmissionRefused("snapshot does not bind exactly one original raw input digest")
    observed = _when(evidence.source_observed_at)
    completed = _when(evidence.latest_completed_event_end)
    if completed > observed:
        raise AdmissionRefused("source bar not completed by its receipt")
    max_end = max(_when(tf.latest_completed_end) for tf in quality)
    if max_end != completed:
        raise AdmissionRefused("raw source and snapshot latest completed bar disagree")
    source_snapshot_asof = _when(snapshot.as_of)
    issued = _now_utc(clock)
    if not (completed <= observed <= source_snapshot_asof <= issued):
        raise AdmissionRefused("first forecast issue must follow all original observed source")
    if (issued - observed).total_seconds() > MAX_CAPTURE_LAG_SECONDS:
        raise AdmissionRefused("live forecast source is stale at issue time")
    if (issued - source_snapshot_asof).total_seconds() > MAX_CAPTURE_LAG_SECONDS:
        raise AdmissionRefused("stale market snapshot cannot be retimestamped as live")

    # The immutable snapshot and its identity MUST both use actual issue UTC
    # so forecast_id cannot collide with older event-time / vintage records.
    issued_snapshot = replace(snapshot, as_of=_stamp(issued))
    record = build_forecast_from_snapshot(issued_snapshot, parameters=parameters)
    if record.created_at_t0 != issued_snapshot.as_of:
        raise AdmissionRefused("forecast was backdated after issuer T0")
    if record.snapshot_id != market_snapshot_id(issued_snapshot) or record.secid != snapshot.secid:
        raise AdmissionRefused("forecast ID and exact instrument are not source-bound")
    if record.validation_status != "UNVALIDATED_BASELINE":
        raise AdmissionRefused("source-bound first issuance is not calibrated model")
    if sorted(h.sessions for h in record.horizons) != [5, 10, 20]:
        raise AdmissionRefused("wrong frozen prospective horizons")
    return PreparedSourceBoundForecast(issued_snapshot, record, evidence)


def capture_prepared_in_staging(prepared: PreparedSourceBoundForecast, bridge: object) -> dict:
    """Journal only through the existing explicit staging-only canonical bridge.

    The bridge itself verifies disposable storage paths and persists original
    receipt before canonical ForecastRecord. This function never opens HOME.
    """
    from birzha.application.prospective_staging_bridge import (
        CanonicalProspectiveStagingBridge,
        STAGING_ONLY,
    )
    if not isinstance(prepared, PreparedSourceBoundForecast):
        raise AdmissionRefused("source-bound issuance must be explicitly prepared")
    if not isinstance(bridge, CanonicalProspectiveStagingBridge) or bridge.mode != STAGING_ONLY:
        raise AdmissionRefused("only an explicit isolated staging bridge is authorized")
    return bridge.capture(prepared.record, prepared.evidence)
