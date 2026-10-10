"""Synthetic causal source-receipt issuance tests, no real-source attestation."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from birzha.application.prospective_capture import AdmissionRefused, CaptureEvidence
from birzha.application.prospective_issuance import (
    prepare_source_bound_forecast, capture_prepared_in_staging,
)
from birzha.domain.snapshot import (
    MarketSnapshot, TimeframeState, TimeframeQuality,
    DataQualityContract, market_snapshot_id,
)


RECEIVED = "2026-10-10T15:00:05Z"
SNAPSHOT_BUILD = "2026-10-10T15:00:08Z"
LATEST_BAR = "2026-10-09T20:59:59Z"
T0 = datetime(2026, 10, 10, 15, 0, 10, tzinfo=timezone.utc)
ORIGINAL_BYTES = b"fixture-original-raw-captured-responses: synthetic, not MOEX"


def evidence(**changes):
    d=dict(source_payload=ORIGINAL_BYTES,
        source_observed_at=RECEIVED,
        latest_completed_event_end=LATEST_BAR,
        source_origin="LIVE_CAPTURED_PAYLOAD",
        contract_version="MARKET_SNAPSHOT_V2")
    d.update(changes)
    return CaptureEvidence(**d)


def state(tf):
    return TimeframeState(
        timeframe=tf, candles=70, last_close=100.0,
        return_5=0.01, return_10=0.02, return_20=0.03,
        sma_20=99.0, sma_50=98.0, efficiency_ratio_20=0.5,
        atr_14_pct=0.012, volume_ratio_20=1.1,
        trend_score=1.0,
    )


def snapshot(**changes):
    digest=sha256(ORIGINAL_BYTES).hexdigest()
    quality=DataQualityContract(
        version="DATA_QUALITY_CONTRACT_V2",
        status="DEGRADED",
        timeframes=tuple(TimeframeQuality(
            timeframe=tf,candles=70,minimum_required=50,
            latest_completed_end=LATEST_BAR,status="PASS"
        ) for tf in ("D1","H1","M15")),
        flow_status="NOT_REQUESTED",
        reasons=("public_source_price_only","independent_calendar_unverified"),
    )
    obj=MarketSnapshot(
        symbol="SBER",secid="SBER",as_of=SNAPSHOT_BUILD,
        source="MOEX_ISS_GOVERNED_OBSERVED_INPUT",
        d1=state("D1"),h1=state("H1"),m15=state("M15"),
        data_quality="DEGRADED",
        quality_contract=quality,
        warnings=("public_source_price_only","independent_calendar_unverified",
                  "source_raw_bundle_sha256="+digest),
    )
    return replace(obj, **changes)


def issue(snap=None, rec=None, clock=lambda:T0):
    return prepare_source_bound_forecast(
        snap if snap is not None else snapshot(),
        rec if rec is not None else evidence(),
        clock=clock,
    )


def test_actual_issuance_clock_not_candle_end_or_reconstruction_clock():
    original=snapshot()
    result=issue(original)
    assert result.snapshot.as_of=="2026-10-10T15:00:10Z"
    assert result.snapshot.as_of!=original.as_of
    assert result.record.created_at_t0==result.snapshot.as_of
    assert result.record.snapshot_id==market_snapshot_id(result.snapshot)
    assert result.record.snapshot_id!=market_snapshot_id(original)
    assert result.record.validation_status=="UNVALIDATED_BASELINE"
    assert {h.sessions for h in result.record.horizons}=={5,10,20}
    assert result.record.secid=="SBER" and result.evidence.source_payload==ORIGINAL_BYTES
    assert original.as_of==SNAPSHOT_BUILD  # never mutate original


def test_stale_observation_cannot_be_reissued_as_new_live_forecast():
    with pytest.raises(AdmissionRefused,match="stale"):
        issue(clock=lambda:T0+timedelta(minutes=6))


def test_event_time_default_snapshot_cannot_enter_first_issuance():
    with pytest.raises(AdmissionRefused,match="legacy/reconstructed"):
        issue(snapshot(source="MOEX_ISS",as_of="2026-10-09T20:59:59Z"))


def test_wrong_raw_bytes_sha_marker_fails_closed():
    with pytest.raises(AdmissionRefused,match="raw input digest"):
        issue(rec=evidence(source_payload=b"replacement-original"))


def test_duplicate_or_missing_raw_source_sha_marker_fails_closed():
    a=snapshot()
    mark=a.warnings[-1]
    with pytest.raises(AdmissionRefused,match="exactly one"):
        issue(replace(a,warnings=a.warnings+(mark,)))
    with pytest.raises(AdmissionRefused,match="exactly one"):
        issue(replace(a,warnings=tuple(x for x in a.warnings if x!=mark)))


def test_last_completed_bar_must_agree_with_all_timeframes_and_receipt():
    with pytest.raises(AdmissionRefused,match="latest completed bar disagree"):
        issue(rec=evidence(latest_completed_event_end="2026-10-09T20:59:58Z"))


def test_issuance_never_precedes_source_observed_or_snapshot_build():
    with pytest.raises(AdmissionRefused,match="must follow"):
        issue(clock=lambda:datetime(2026,10,10,15,0,6,tzinfo=timezone.utc))
    with pytest.raises(AdmissionRefused,match="must follow"):
        issue(rec=evidence(source_observed_at="2026-10-10T15:01:00Z"))


def test_invalid_naive_snapshot_receipt_and_issuer_timestamps_rejected():
    with pytest.raises(AdmissionRefused,match="explicit timezone"):
        issue(snapshot(as_of="2026-10-10 15:00:08"))
    with pytest.raises(AdmissionRefused,match="explicit timezone"):
        issue(rec=evidence(source_observed_at="2026-10-10 15:00:05"))
    with pytest.raises(AdmissionRefused,match="timezone-aware"):
        issue(clock=lambda:datetime(2026,10,10,15,0,10))


def test_zero_incomplete_or_inconsistent_timeframes_are_rejected():
    a=snapshot()
    q=a.quality_contract
    assert q is not None
    broken=replace(q,timeframes=q.timeframes[:2])
    with pytest.raises(AdmissionRefused,match="exact D1/H1/M15"):
        issue(replace(a,quality_contract=broken))
    wrong=replace(q,timeframes=(
        replace(q.timeframes[0],candles=49,status="DEGRADED"),
        *q.timeframes[1:]))
    with pytest.raises(AdmissionRefused,match="insufficient completed"):
        issue(replace(a,quality_contract=wrong))
    mismatch=replace(a,d1=replace(a.d1,candles=71))
    with pytest.raises(AdmissionRefused,match="count inconsistent"):
        issue(mismatch)


def test_unreceipted_volume_profile_and_unverified_source_declined():
    with pytest.raises(AdmissionRefused,match="unreceipted"):
        issue(snapshot(volume_profile=object()))
    with pytest.raises(AdmissionRefused,match="legacy/reconstructed"):
        issue(rec=evidence(source_origin="SYNTHETIC_INPUT"))


def test_missing_provider_authentication_is_not_misreported_as_attested():
    result=issue()
    assert not any("independently_attested" in warning for warning in result.record.warnings)
    assert result.record.validation_status=="UNVALIDATED_BASELINE"
    assert result.snapshot.data_quality=="DEGRADED"
    assert result.snapshot.quality_contract.flow_status=="NOT_REQUESTED"


def test_capture_is_explicitly_staging_only_and_refuses_other_writer():
    result=issue()
    with pytest.raises(AdmissionRefused,match="explicit isolated staging bridge"):
        capture_prepared_in_staging(result,object())
