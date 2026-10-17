"""Bounded, synthetic, no-data/no-provider tests for Research factor fusion."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from birzha.application.g2_factor_fusion_research import (
    FactorEvidence,
    VERSION,
    compare_factor_combinations,
)

T0 = "2026-10-17T12:00:00Z"
HASH = "a" * 64


def item(family: str, signed: float | None, *, status: str = "AVAILABLE",
         event: str | None = "2026-10-17T11:00:00Z",
         observed: str | None = "2026-10-17T11:05:00Z",
         sha: str | None = HASH) -> FactorEvidence:
    return FactorEvidence(family, status, signed, event, observed, sha)


def run(*rows: FactorEvidence, mode: str = "RECONSTRUCTED_RESEARCH_ONLY"):
    return compare_factor_combinations(as_of=T0, evidence=tuple(rows), mode=mode)


def test_multiple_independent_groups_and_consensus():
    result = run(
        item("market_state", 0.8),
        item("control", 0.7),
        item("pressure", 0.7),
        item("effort_result", 0.9),  # shares FLOW group with pressure
        item("route", 0.8),          # veto only; not a fourth vote
        item("efficiency", None),    # unsigned context
    )
    assert result["version"] == VERSION
    assert result["counted_vote_groups"] == 3
    assert result["group_votes"]["FLOW"] == 0.8
    assert all(v["candidate_direction"] == "UP" for v in result["models"].values())
    assert all(v["operational_decision"] == "NOT_AUTHORIZED" for v in result["models"].values())
    assert all(v["probability"] is None for v in result["models"].values())


def test_duplicate_family_rejected_not_extra_vote():
    with pytest.raises(ValueError, match="repeated factor"):
        run(item("market_state", 0.8), item("market_state", 0.8))


def test_unsigned_efficiency_must_not_vote():
    with pytest.raises(ValueError, match="unsigned context"):
        run(item("efficiency", 0.7))


def test_baseline_versions_not_independent_factors():
    with pytest.raises(ValueError, match="unsupported"):
        run(item("forecast_v04", 0.8))


def test_missing_control_research_only_with_consensus_abstaining():
    result = run(item("market_state", -0.8), item("pressure", -0.7), item("route", -0.9))
    assert result["models"]["GROUP_MEAN"]["candidate_direction"] == "DOWN"
    assert result["models"]["STATE_CONTROL_ROUTE_CONSENSUS"]["candidate_direction"] == "ABSTAIN"


def test_mixed_conflicting_direction_rejects_group_mean():
    result = run(item("market_state", 0.95), item("control", 0.85), item("pressure", -1.0),
                 item("route", 0.8))
    assert result["models"]["GROUP_MEAN"]["candidate_direction"] == "ABSTAIN"
    assert result["models"]["STATE_CONTROL_ROUTE_CONSENSUS"]["reason"] == "STRONG_OPPOSING_PRESSURE"


def test_degraded_volatility_vetoes_all_candidate_directions():
    result = run(item("market_state", 0.8), item("control", 0.8), item("route", 0.8),
                 item("volatility", None, status="DEGRADED"))
    assert all(x["candidate_direction"] == "ABSTAIN" for x in result["models"].values())
    assert all(x["reason"] == "VOLATILITY_RISK_VETO" for x in result["models"].values())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), True, 1.1, -1.1])
def test_nonfinite_boolean_out_of_range_rejected(value):
    with pytest.raises(ValueError, match="signed strength"):
        run(item("market_state", value))


def test_no_future_event_or_receipt():
    with pytest.raises(ValueError, match="future event"):
        run(item("market_state", 0.8, event="2026-10-17T12:00:01Z"))
    with pytest.raises(ValueError, match="post-decision"):
        run(item("market_state", 0.8, observed="2026-10-17T12:00:01Z"))


def test_no_naive_or_receipt_before_event():
    with pytest.raises(ValueError, match="naive"):
        run(item("market_state", 0.8, observed="2026-10-17T11:05:00"))
    with pytest.raises(ValueError, match="received before"):
        run(item("market_state", 0.8, observed="2026-10-17T10:05:00"))


def test_reconstructed_missing_receipt_does_not_become_strict():
    evidence = (item("market_state", 0.8, observed=None, event=None, sha=None),)
    result = compare_factor_combinations(as_of=T0, evidence=evidence)
    assert result["mode"] == "RECONSTRUCTED_RESEARCH_ONLY"
    with pytest.raises(ValueError, match="prospective evidence"):
        compare_factor_combinations(
            as_of=T0, evidence=evidence, mode="PROSPECTIVE_RECEIPT_RESEARCH"
        )


def test_prospective_receipt_is_not_provider_attestation():
    result = run(item("market_state", 0.8), item("control", 0.8),
                 mode="PROSPECTIVE_RECEIPT_RESEARCH")
    assert result["source_attestation"] == "NOT_INDEPENDENTLY_VERIFIED"
    assert result["models"]["GROUP_MAJORITY"]["operational_decision"] == "NOT_AUTHORIZED"


def test_unavailable_and_proxy_never_fake_zeros_or_votes():
    result = run(item("market_state", 0.8), item("control", None, status="UNAVAILABLE",
                 sha=None, observed=None, event=None), item("route", None, status="PROXY",
                 sha=None, observed=None, event=None))
    assert result["counted_vote_groups"] == 1
    assert all(x["candidate_direction"] == "ABSTAIN" for x in result["models"].values())
    assert result["factor_statuses"]["control"] == "UNAVAILABLE"


def test_immutable_order_independent_hash_and_output():
    rows = (item("market_state", 0.8), item("pressure", 0.6), item("control", 0.8),
            item("route", 0.8))
    a = compare_factor_combinations(as_of=T0, evidence=rows)
    b = compare_factor_combinations(as_of=T0, evidence=tuple(reversed(rows)))
    assert a == b
    assert a["input_sha256"] == b["input_sha256"]


def test_bad_identity_or_invalid_status_fail_closed():
    with pytest.raises(ValueError, match="invalid source SHA"):
        run(item("market_state", 0.8, sha="not-a-hash"))
    with pytest.raises(ValueError, match="unavailable"):
        run(item("market_state", 0.8, status="DEGRADED"))
    with pytest.raises(ValueError, match="unknown research evidence mode"):
        run(item("market_state", 0.8), mode="STRICT_CAUSAL_REPLAY")


def test_unknown_t0_timezone_rejected():
    with pytest.raises(ValueError, match="naive"):
        compare_factor_combinations(as_of="2026-10-17T12:00:00", evidence=(item("market_state", 0.8),))
