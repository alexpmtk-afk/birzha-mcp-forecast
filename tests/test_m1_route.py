"""Synthetic-only M1 route semantics: not price forecasting or data attestation."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from birzha.application.m1_route import RoutePrice, RouteZone, analyze_m1_route

T0 = datetime(2026, 10, 19, 15, tzinfo=timezone.utc)


def price(value=100, **kw):
    return replace(RoutePrice("SBER", value, T0 - timedelta(minutes=2),
                              T0 - timedelta(minutes=1), "synthetic:p0"), **kw)


def zone(name="R1", lower=105, upper=108, **kw):
    return replace(RouteZone(
        name, "SBER", "H1", lower, upper, "SWING", "RESISTANCE", "CONFIRMED",
        "PRICE_STRUCTURE", T0 - timedelta(days=3), T0 - timedelta(days=2),
        T0 - timedelta(days=1), "synthetic:zone:" + name), **kw)


def run(zones=None, scopes=None, direction="UP", p0=None, t0=T0):
    return analyze_m1_route(
        t0=t0, p0=p0 or price(), direction=direction,
        zones=[zone()] if zones is None else zones,
        horizon_scopes={5: 3., 10: 8., 20: 20.} if scopes is None else scopes)


def test_independent_horizon_envelopes_do_not_clone_h20():
    r = run()["horizons"]
    assert r["H5"]["status"] == "UNKNOWN_COVERAGE"
    assert r["H10"]["nearest_obstacle"]["level_id"] == "R1"
    assert r["H20"]["obstacle_count"] == 1
    assert r["H5"]["forecast_allowed"] is False


def test_up_distance_is_to_nearest_lower_zone_boundary():
    r = run()["horizons"]["H10"]["nearest_obstacle"]
    assert r["distance_to_nearest_boundary"] == 5
    assert r["lower"] == 105 and r["upper"] == 108


def test_down_distance_is_to_upper_zone_boundary():
    z = zone("S", 91, 96, role="SUPPORT")
    result = run([z], direction="DOWN")["horizons"]["H10"]
    assert result["nearest_obstacle"]["distance_to_nearest_boundary"] == 4
    assert result["status"] == "STRUCTURAL_OBSTACLE"


def test_inside_zone_reports_zero_not_center_distance():
    r = run([zone(lower=99, upper=102)])["horizons"]["H5"]
    assert r["nearest_obstacle"]["distance_to_nearest_boundary"] == 0
    assert r["nearest_obstacle"]["inside_zone"] is True


def test_exact_boundary_is_zero_distance():
    z = zone(lower=100, upper=102)
    assert run([z])["horizons"]["H5"]["nearest_obstacle"]["distance_to_nearest_boundary"] == 0


def test_behind_only_does_not_claim_infinite_empty_route():
    r = run([zone(lower=90, upper=95)])["horizons"]
    assert all(v["status"] == "UNKNOWN_COVERAGE" for v in r.values())


def test_no_levels_is_unknown_coverage():
    r = run([])
    assert all(v["status"] == "UNKNOWN_COVERAGE" for v in r["horizons"].values())
    assert r["coverage_attested"] is False
    assert r["forecast_allowed"] is False


def test_profile_not_admitted_as_mandatory_obstacle():
    z = zone("POC", 101, 101, level_type="POC", source_type="CANDLE_VOLUME_PROXY")
    r = run([z])
    assert r["profile_context_level_ids"] == ("POC",)
    assert r["horizons"]["H20"]["status"] == "UNKNOWN_COVERAGE"


def test_exact_trade_based_profile_also_only_diagnostic():
    z = zone("VW", 101, 103, level_type="HVN", source_type="TRADES_BASED")
    assert run([z])["horizons"]["H10"]["nearest_obstacle"] is None


def test_wrong_role_is_not_an_upward_obstacle():
    z = zone(role="SUPPORT")
    assert run([z])["horizons"]["H10"]["status"] == "UNKNOWN_COVERAGE"


def test_unresolved_role_nearer_blocks_claimed_nearest():
    z0 = zone("unknown", 101, 102, role="UNKNOWN")
    z1 = zone("confirmed", 105, 107)
    r = run([z1, z0])["horizons"]["H10"]
    assert r["status"] == "UNRESOLVED_FORWARD_ZONE"
    assert r["nearest_obstacle"]["level_id"] == "confirmed"
    assert r["unresolved_level_ids"] == ("unknown",)


def test_provisional_quality_is_not_confirmed_barrier():
    z = zone(quality="PROVISIONAL")
    r = run([z])["horizons"]["H10"]
    assert r["status"] == "UNRESOLVED_FORWARD_ZONE"
    assert r["nearest_obstacle"] is None


def test_provisional_zone_farther_does_not_hide_nearest_confirmed():
    r = run([zone("far", 107, 108, quality="PROVISIONAL"), zone("near", 103, 104)])
    assert r["horizons"]["H10"]["status"] == "STRUCTURAL_OBSTACLE"
    assert r["horizons"]["H10"]["nearest_obstacle"]["level_id"] == "near"


def test_price_then_time_then_id_tie_break():
    a = zone("B", 105, 107)
    b = zone("A", 105, 106)
    assert run([a, b])["horizons"]["H10"]["nearest_obstacle"]["level_id"] == "A"
    newer = replace(b, confirmed_at=a.confirmed_at + timedelta(minutes=1))
    assert run([newer, a])["horizons"]["H10"]["nearest_obstacle"]["level_id"] == "B"
    older = replace(b, confirmed_at=a.confirmed_at - timedelta(minutes=1))
    assert run([a, older])["horizons"]["H10"]["nearest_obstacle"]["level_id"] == "A"


def test_future_level_is_invisible_at_old_t0_and_no_future_leak():
    baseline = run([zone("old")])
    future = zone("future", 101, 102, confirmed_at=T0 + timedelta(hours=1),
                  available_at=T0 + timedelta(hours=2))
    assert run([future, zone("old")]) == baseline


def test_late_backfilled_level_is_invisible_until_available():
    late = zone("late", 101, 102, available_at=T0 + timedelta(seconds=1))
    assert run([late])["horizons"]["H20"]["status"] == "UNKNOWN_COVERAGE"


def test_two_contracts_cannot_be_mixed():
    with pytest.raises(ValueError, match="contract"):
        run([zone(secid="SiZ6")])


def test_duplicate_level_id_rejected_no_silent_merge():
    with pytest.raises(ValueError, match="duplicate"):
        run([zone(), zone(lower=103)])


def test_invalid_price_and_nan_zone_rejected():
    with pytest.raises(ValueError, match="finite"):
        run(p0=price(float("nan")))
    with pytest.raises(ValueError, match="finite price zone"):
        run([zone(lower=float("inf"))])
    with pytest.raises(ValueError, match="finite price zone"):
        run([zone(lower=110, upper=108)])


def test_price_provenance_cannot_arrive_after_t0():
    with pytest.raises(ValueError, match="known by T0"):
        run(p0=price(available_at=T0 + timedelta(seconds=1)))
    with pytest.raises(ValueError, match="known by T0"):
        run(p0=price(source_ref=" "))


def test_unavailable_zone_causality_must_be_valid():
    with pytest.raises(ValueError, match="impossible causal"):
        run([zone(confirmed_at=T0 - timedelta(days=4))])


def test_missing_independent_horizon_is_config_required():
    r = run(scopes={5: 3., 20: 20.})["horizons"]
    assert r["H10"]["status"] == "CONFIG_REQUIRED"
    assert r["H5"]["status"] == "UNKNOWN_COVERAGE"
    assert r["H20"]["status"] == "STRUCTURAL_OBSTACLE"


def test_invalid_horizon_envelope_is_refused():
    for bad in (0, -5, float("nan"), True, float("inf")):
        with pytest.raises(ValueError, match="positive finite"):
            run(scopes={5: bad, 10: 8., 20: 20.})
    with pytest.raises(ValueError, match="unsupported horizon"):
        run(scopes={7: 10.})


def test_roles_unknown_at_t0_cannot_be_backdated():
    z = zone(role="UNKNOWN")
    assert run([z])["horizons"]["H10"]["status"] == "UNRESOLVED_FORWARD_ZONE"


def test_determinism_independent_of_zone_order():
    z = [zone("A", 103, 104), zone("B", 108, 109), zone("C", 106, 107)]
    assert run(z) == run(list(reversed(z)))
