"""Synthetic transition examples for M1 revision 2.2 (not market backtests)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from birzha.application.m1_mandatory_chain import (
    ChainContext, Evidence, HORIZONS, HORIZON_KEYS, SHARED_KEYS,
    audit_m1_mandatory_chain,
)

T0 = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)


def _true(key="sample"):
    return Evidence(
        "TRUE", source_ref="synthetic:" + key,
        event_time=T0 - timedelta(hours=5),
        confirmed_at=T0 - timedelta(hours=4),
        available_at=T0 - timedelta(hours=3),
    )


def _example(direction="LONG"):
    ascending = direction == "LONG"
    context = ChainContext(
        t0=T0, direction=direction,
        d1_state="TREND_UP" if ascending else "TREND_DOWN",
        h1_side="BUYERS" if ascending else "SELLERS",
        h1_strength="RETESTED", h1_causal_status="CONFIRMED",
        h1_level_id="level-confirmed-at-t0", m15_level_id="level-confirmed-at-t0",
        d1_secid="SBER", h1_secid="SBER", m15_secid="SBER",
        event_flag=False,
    )
    shared = {key: _true(key) for key in SHARED_KEYS}
    horizons = {h: {key: _true(f"H{h}-{key}") for key in HORIZON_KEYS} for h in HORIZONS}
    return context, shared, horizons


def _audit(example):
    return audit_m1_mandatory_chain(*example)


def test_mirror_long_and_short_are_symmetric_and_never_executable():
    for direction in ("LONG", "SHORT"):
        report = _audit(_example(direction))
        assert report["status"] == "DIAGNOSTIC_ONLY"
        assert report["evidence_attested"] is False
        assert report["forecast_allowed"] is False
        for h in HORIZONS:
            assert report["horizons"][f"H{h}"]["state"] == "CHAIN_CONSISTENT_UNVERIFIED"
            assert report["horizons"][f"H{h}"]["forecast_allowed"] is False


def test_initial_control_does_not_pass_any_horizon():
    ctx, shared, horizons = _example()
    report = _audit((replace(ctx, h1_strength="INITIAL"), shared, horizons))
    assert all(v["state"] == "CONDITION_FALSE" for v in report["horizons"].values())


def test_pending_control_is_not_confirmed():
    ctx, shared, horizons = _example()
    report = _audit((replace(ctx, h1_causal_status="PENDING"), shared, horizons))
    assert all(v["state"] == "EVIDENCE_UNAVAILABLE" for v in report["horizons"].values())


def test_no_level_retest_identity_blocks_every_horizon():
    ctx, shared, horizons = _example()
    report = _audit((replace(ctx, m15_level_id="different-level"), shared, horizons))
    assert all(v["state"] == "CONDITION_FALSE" for v in report["horizons"].values())


def test_m15_missing_level_is_unavailable_not_false():
    ctx, shared, horizons = _example()
    report = _audit((replace(ctx, m15_level_id=None), shared, horizons))
    assert all(v["state"] == "EVIDENCE_UNAVAILABLE" for v in report["horizons"].values())


def test_event_blocks_all_horizons_without_fabricating_a_market_regime():
    ctx, shared, horizons = _example()
    report = _audit((replace(ctx, event_flag=True), shared, horizons))
    assert all(v["state"] == "CONDITION_FALSE" for v in report["horizons"].values())


def test_unknown_event_is_not_neutralized():
    ctx, shared, horizons = _example()
    report = _audit((replace(ctx, event_flag=None), shared, horizons))
    assert all(v["state"] == "EVIDENCE_UNAVAILABLE" for v in report["horizons"].values())


def test_cross_contract_contamination_fails_closed():
    ctx, shared, horizons = _example()
    report = _audit((replace(ctx, m15_secid="SiZ6"), shared, horizons))
    assert all(v["state"] == "CONDITION_FALSE" for v in report["horizons"].values())


def test_direction_mismatch_fails_closed():
    ctx, shared, horizons = _example("SHORT")
    report = _audit((replace(ctx, d1_state="TREND_UP"), shared, horizons))
    assert all(v["state"] == "CONDITION_FALSE" for v in report["horizons"].values())


def test_missing_anchor_is_unavailable():
    ctx, shared, horizons = _example()
    shared.pop("anchor_confirmed")
    report = _audit((ctx, shared, horizons))
    assert all(v["state"] == "EVIDENCE_UNAVAILABLE" for v in report["horizons"].values())


def test_false_h10_target_does_not_change_h5_or_h20():
    ctx, shared, horizons = _example()
    horizons[10]["target_reachable"] = Evidence("FALSE")
    report = _audit((ctx, shared, horizons))["horizons"]
    assert [report[f"H{h}"]["state"] for h in HORIZONS] == [
        "CHAIN_CONSISTENT_UNVERIFIED", "CONDITION_FALSE", "CHAIN_CONSISTENT_UNVERIFIED",
    ]


def test_missing_h20_is_not_substituted_from_h5():
    ctx, shared, horizons = _example()
    horizons.pop(20)
    report = _audit((ctx, shared, horizons))["horizons"]
    assert report["H5"]["state"] == "CHAIN_CONSISTENT_UNVERIFIED"
    assert report["H20"]["state"] == "EVIDENCE_UNAVAILABLE"


def test_future_information_cannot_be_used_for_t0():
    ctx, shared, horizons = _example()
    shared["anchor_confirmed"] = replace(_true(), available_at=T0 + timedelta(seconds=1))
    report = _audit((ctx, shared, horizons))
    assert all(v["state"] == "EVIDENCE_UNAVAILABLE" for v in report["horizons"].values())


def test_observation_must_be_confirmed_before_available():
    ctx, shared, horizons = _example()
    shared["calendar_admissible"] = replace(_true(), confirmed_at=T0 - timedelta(hours=1))
    report = _audit((ctx, shared, horizons))
    assert all(v["state"] == "EVIDENCE_UNAVAILABLE" for v in report["horizons"].values())


def test_true_without_source_reference_is_not_proof():
    ctx, shared, horizons = _example()
    shared["m15_same_level_retest_hold"] = replace(_true(), source_ref="")
    report = _audit((ctx, shared, horizons))
    assert all(v["state"] == "EVIDENCE_UNAVAILABLE" for v in report["horizons"].values())


def test_missing_horizon_component_is_unavailable():
    ctx, shared, horizons = _example()
    horizons[5].pop("expiry_defined")
    report = _audit((ctx, shared, horizons))["horizons"]
    assert report["H5"]["state"] == "EVIDENCE_UNAVAILABLE"
    assert report["H10"]["state"] == "CHAIN_CONSISTENT_UNVERIFIED"


def test_naive_t0_is_rejected():
    ctx, shared, horizons = _example()
    with pytest.raises(ValueError, match="timezone-aware"):
        _audit((replace(ctx, t0=T0.replace(tzinfo=None)), shared, horizons))


def test_earlier_availability_after_t0_rejected_on_h10_alone():
    ctx, shared, horizons = _example()
    horizons[10]["route_usable"] = replace(_true(), available_at=T0 + timedelta(days=1))
    report = _audit((ctx, shared, horizons))["horizons"]
    assert report["H10"]["state"] == "EVIDENCE_UNAVAILABLE"
    assert report["H5"]["state"] == "CHAIN_CONSISTENT_UNVERIFIED"


def test_repeatability_of_frozen_synthetic_inputs():
    example = _example()
    assert _audit(example) == _audit(example)
