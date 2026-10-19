"""Synthetic causal control tests: no MOEX, OOS, provider or operational forecast."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from birzha.application.m1_causal_swings import CausalBar
from birzha.application.m1_control_chain import (
    AnchorBinding, ControlParameters, FrozenLevel, scan_m1_control_chain,
)

START = datetime(2026, 10, 19, 0, tzinfo=timezone.utc)


def _bar(i, high, low, close, *, open_price=None):
    s = START + timedelta(hours=i)
    return CausalBar(
        secid="SBER", timeframe="H1", start=s, end=s + timedelta(hours=1),
        available_at=s + timedelta(hours=1, seconds=1),
        open=close if open_price is None else open_price,
        high=high, low=low, close=close, is_complete=True,
        source_ref=f"synthetic:{i}",
    )


def _bars():
    return [
        _bar(0, 101.5, 99.5, 100.5),  # PROBE only
        _bar(1, 103.5, 100.5, 103.0),  # ACCEPTANCE
        _bar(2, 104.0, 102.0, 103.5),  # PERSISTENCE 1
        _bar(3, 104.5, 102.5, 104.0),  # PERSISTENCE 2
        _bar(4, 103.5, 100.5, 103.0),  # RETEST (intersects zone, closes above)
        _bar(5, 104.0, 101.5, 103.2),  # HOLD 1
        _bar(6, 104.2, 101.7, 103.6),  # HOLD 2
    ]


def _anchor(side="BUYERS"):
    return AnchorBinding(
        secid="SBER", timeframe="D1", direction="UP" if side == "BUYERS" else "DOWN",
        event_time=START - timedelta(hours=9),
        confirmed_at=START - timedelta(hours=8),
        available_at=START - timedelta(hours=7),
        source_ref="synthetic:anchor",
    )


def _level(side="BUYERS"):
    return FrozenLevel(
        level_id="structure-L-42", secid="SBER", timeframe="H1",
        lower=100.0 if side == "BUYERS" else 99.0,
        upper=101.0 if side == "BUYERS" else 100.0,
        atr_frozen=2.0,
        event_time=START - timedelta(hours=6),
        confirmed_at=START - timedelta(hours=5),
        available_at=START - timedelta(hours=4),
        source_ref="synthetic:level",
    )


def _parameters(**changes):
    return replace(ControlParameters(
        confirm_bars=2, hold_bars=2, reclaim_buffer_atr=0.5, chain_max_age=12,
    ), **changes)


def _run(bars=None, *, t0=None, side="BUYERS", anchor=None, level=None, parameters=None):
    bars = _bars() if bars is None else bars
    if t0 is None:
        t0 = bars[-1].available_at if bars else START
    return scan_m1_control_chain(
        bars, secid="SBER", timeframe="H1", t0=t0, side=side,
        anchor=anchor or _anchor(side), level=level or _level(side),
        parameters=parameters or _parameters(),
    )


def _mirror(bars):
    return [replace(b, high=200-b.low, low=200-b.high, open=200-b.open,
                    close=200-b.close) for b in bars]


def test_buyers_five_distinct_stages_with_observed_retested_control():
    r = _run()
    assert r["status"] == "CONFIRMED_RESEARCH_ONLY"
    assert (r["control_side"], r["control_strength"], r["causal_status"]) == (
        "BUYERS", "RETESTED", "CONFIRMED",
    )
    assert r["stage"] == "RETESTED"
    assert [r["event_times"][name]["index"] for name in (
        "PROBE", "ACCEPTANCE", "PERSISTENCE", "RETEST", "HOLD"
    )] == [0, 1, 3, 4, 6]
    assert r["confirmed_at"] == _bars()[6].end
    assert r["available_at"] == _bars()[6].available_at
    assert r["checked_non_reclaim_through"] == _bars()[6].end
    assert r["source_receipts_attested"] is False
    assert r["forecast_allowed"] is False


def test_sellers_mirror_has_same_causal_stages():
    r = _run(_mirror(_bars()), side="SELLERS")
    assert r["control_strength"] == "RETESTED"
    assert r["control_side"] == "SELLERS"
    assert [r["event_times"][name]["index"] for name in (
        "PROBE", "ACCEPTANCE", "PERSISTENCE", "RETEST", "HOLD"
    )] == [0, 1, 3, 4, 6]


def test_initial_after_persistence_not_eligible_as_retested():
    r = _run(_bars()[:4])
    assert r["status"] == "PENDING"
    assert r["control_strength"] == "INITIAL"
    assert r["causal_status"] == "PENDING"
    assert r["stage"] == "RETEST_PENDING"


def test_retest_without_subsequent_hold_is_only_initial():
    r = _run(_bars()[:5])
    assert r["stage"] == "HOLD_PENDING"
    assert r["control_strength"] == "INITIAL"
    assert "HOLD" not in r["event_times"]


def test_retest_bar_cannot_be_first_hold_bar():
    r = _run(_bars()[:6])
    assert r["stage"] == "HOLD_PENDING"
    assert "HOLD" not in r["event_times"]


def test_acceptance_cannot_share_bar_with_probe():
    bars = [_bar(0, 103, 99, 102), _bar(1, 104, 102, 103)]
    r = _run(bars)
    assert r["status"] == "NO_CHAIN"
    assert r["event_times"] == {}


def test_touch_and_close_inside_zone_cannot_complete_retest():
    bars = _bars()
    bars[4] = _bar(4, 103.5, 100.5, 100.5)
    r = _run(bars)
    assert r["control_strength"] == "NONE"
    assert r["last_reset_reason"] == "RETEST_NOT_ACCEPTED"


def test_close_equal_to_accepted_boundary_breaks_hold():
    bars = _bars()
    bars[5] = _bar(5, 104.0, 101.0, 101.0)
    r = _run(bars)
    assert r["control_strength"] == "NONE"
    assert r["last_reset_reason"] == "HOLD_INTERRUPTED"


def test_interrupted_persistence_revokes_initial_chain():
    bars = _bars()
    bars[2] = _bar(2, 103, 100.5, 100.8)
    r = _run(bars)
    assert r["control_strength"] == "NONE"
    assert r["last_reset_reason"] == "PERSISTENCE_INTERRUPTED"


def test_opposite_side_reclaim_after_hold_revokes_control():
    bars = _bars() + [_bar(7, 102, 97, 98.9)]
    r = _run(bars)
    assert r["control_strength"] == "NONE"
    assert r["last_reset_reason"] == "RECLAIMED_AFTER_ACCEPTANCE"


def test_reclaim_boundary_equality_is_not_reclaim():
    # Lower=100, ATR=2 and buffer=0.5 => threshold=99.0 exactly.
    bars = _bars() + [_bar(7, 102, 98.5, 99.0)]
    r = _run(bars)
    assert r["control_strength"] == "RETESTED"
    assert r["checked_non_reclaim_through"] == bars[-1].end


def test_sellers_reclaim_above_opposite_side_revokes():
    bars = _mirror(_bars()) + [_bar(7, 102, 98, 101.1)]
    r = _run(bars, side="SELLERS")
    assert r["last_reset_reason"] == "RECLAIMED_AFTER_ACCEPTANCE"
    assert r["control_strength"] == "NONE"


def test_new_chain_must_reprobe_after_reclaim():
    bars = _bars() + [
        _bar(7, 102, 97, 98.9),    # invalidate
        _bar(8, 101, 99.5, 100.5), # fresh probe
        _bar(9, 103.5, 100.5, 103),
        _bar(10, 104, 102, 103.5),
        _bar(11, 104, 102, 103.5),
        _bar(12, 103, 100.5, 102),
        _bar(13, 104, 101.5, 103),
        _bar(14, 104, 101.5, 103),
    ]
    r = _run(bars)
    assert r["control_strength"] == "RETESTED"
    assert r["event_times"]["PROBE"]["index"] == 8
    assert r["last_reset_reason"] == "RECLAIMED_AFTER_ACCEPTANCE"


def test_expiration_uses_explicit_bar_age_and_never_promotes():
    r = _run(parameters=_parameters(chain_max_age=5))
    assert r["control_strength"] == "NONE"
    assert r["last_reset_reason"] == "CHAIN_EXPIRED"


def test_append_future_bars_does_not_change_old_t0_answer():
    bars = _bars()
    t0 = bars[-1].available_at
    future = _bar(7, 102, 97, 98.9)
    assert _run(bars, t0=t0) == _run(bars + [future], t0=t0)


def test_late_receipt_fails_closed_even_if_ohlc_is_good():
    bars = _bars()
    t0 = bars[-1].available_at
    bars[3] = replace(bars[3], available_at=t0 + timedelta(minutes=1))
    r = _run(bars, t0=t0)
    assert r["status"] == "INPUT_UNAVAILABLE"
    assert r["control_strength"] == "NONE"


def test_missing_source_is_not_evidence():
    bars = _bars()
    bars[4] = replace(bars[4], source_ref=" ")
    assert _run(bars)["status"] == "INPUT_UNAVAILABLE"


def test_incomplete_bar_not_accepted_or_skipped():
    bars = _bars()
    bars[4] = replace(bars[4], is_complete=False)
    assert _run(bars)["status"] == "INPUT_UNAVAILABLE"


def test_mixed_contract_at_any_stage_is_rejected():
    bars = _bars()
    bars[3] = replace(bars[3], secid="SiZ6")
    with pytest.raises(ValueError, match="contract"):
        _run(bars)


def test_frozen_level_must_be_available_before_probe():
    later = replace(_level(), available_at=START + timedelta(hours=1))
    # The first probe is not yet admissible; the next bar is accepted-side,
    # so it cannot retroactively count as an earlier probe.
    r = _run(level=later)
    assert r["control_strength"] == "NONE"


def test_anchor_must_be_confirmed_before_price_control():
    later = replace(
        _anchor(),
        confirmed_at=START + timedelta(hours=1),
        available_at=START + timedelta(hours=2),
    )
    assert _run(anchor=later)["control_strength"] == "NONE"


def test_unconfirmed_or_future_level_is_rejected():
    with pytest.raises(ValueError, match="noncausal"):
        _run(level=replace(_level(), available_at=_bars()[-1].available_at + timedelta(days=1)))


def test_wrong_anchor_side_is_rejected():
    with pytest.raises(ValueError, match="direction"):
        _run(anchor=_anchor("SELLERS"))


def test_invalid_level_atr_rejected():
    with pytest.raises(ValueError, match="frozen ATR"):
        _run(level=replace(_level(), atr_frozen=0))


def test_invalid_parameters_do_not_get_defaulted():
    for cfg in (
        _parameters(confirm_bars=0),
        _parameters(hold_bars=True),
        _parameters(chain_max_age=-1),
        _parameters(reclaim_buffer_atr=float("nan")),
        _parameters(reclaim_buffer_atr=-0.1),
    ):
        with pytest.raises(ValueError):
            _run(parameters=cfg)


def test_t0_must_be_timezone_aware():
    with pytest.raises(ValueError, match="timezone-aware"):
        _run(t0=START.replace(tzinfo=None))


def test_repeated_input_is_deterministic():
    assert _run() == _run()



from birzha.application.m1_causal_anchor import (
    AnchorParameters, scan_m1_consolidation_anchors,
)
from birzha.application.m1_control_chain import bind_m1_research_anchor


def _algorithmic_anchor_result():
    history = [_bar(i - 35, 101, 99, 100) for i in range(20)]
    history += [
        _bar(-15, 105, 99, 104),
        _bar(-14, 106, 103, 105),
    ]
    return scan_m1_consolidation_anchors(
        history, secid="SBER", timeframe="H1", t0=history[-1].available_at,
        parameters=AnchorParameters(
            consolidation_windows=(5, 7), er_balance=0.25,
            range_balance_atr=2.0, breakout_buffer_atr=0.5,
            confirm_bars=2, reclaim_buffer_atr=0.5,
        ),
    )


def test_control_binds_actual_causal_anchor_scanner_output():
    source = _algorithmic_anchor_result()
    assert source["status"] == "CONFIRMED_RESEARCH_ONLY"
    binding = bind_m1_research_anchor(source)
    assert binding.direction == "UP"
    assert binding.available_at < _bars()[0].start
    assert binding.evidence_refs == ("synthetic:-15", "synthetic:-14")
    r = _run(anchor=binding)
    assert r["control_strength"] == "RETESTED"
    assert r["anchor_evidence_refs"] == binding.evidence_refs
    assert r["source_receipts_attested"] is False
    assert not r["forecast_allowed"]


def test_research_anchor_adapter_refuses_pending_anchor():
    source = _algorithmic_anchor_result()
    source["status"] = "PENDING"
    with pytest.raises(ValueError, match="confirmed research"):
        bind_m1_research_anchor(source)


def test_research_anchor_adapter_refuses_missing_causal_refs():
    source = _algorithmic_anchor_result()
    candidate = dict(source["confirmed_anchor"])
    candidate["confirmation_source_refs"] = ()
    source["confirmed_anchor"] = candidate
    with pytest.raises(ValueError, match="references"):
        bind_m1_research_anchor(source)


def test_research_anchor_adapter_rejects_future_confirmation_times():
    source = _algorithmic_anchor_result()
    candidate = dict(source["confirmed_anchor"])
    candidate["available_at"] = candidate["confirmed_at"] - timedelta(hours=1)
    source["confirmed_anchor"] = candidate
    with pytest.raises(ValueError, match="causal fields"):
        bind_m1_research_anchor(source)
