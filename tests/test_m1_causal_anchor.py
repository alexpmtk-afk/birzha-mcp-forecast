"""Synthetic-only M1 v2.2 anchors: no real history, holdout, or fitted parameters."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from birzha.application.m1_causal_anchor import (
    AnchorParameters, scan_m1_consolidation_anchors,
)
from birzha.application.m1_causal_swings import CausalBar

START = datetime(2026, 10, 19, 0, tzinfo=timezone.utc)


def _bar(index, *, o=100.0, h=101.0, l=99.0, c=100.0):
    start = START + timedelta(hours=index)
    end = start + timedelta(hours=1)
    return CausalBar(
        secid="SBER", timeframe="H1", start=start, end=end,
        available_at=end + timedelta(seconds=1),
        open=o, high=h, low=l, close=c,
        is_complete=True, source_ref=f"synthetic:{index}",
    )


def _series(side="UP", *, second=True):
    bars = [_bar(i) for i in range(20)]
    if side == "UP":
        bars.append(_bar(20, o=100, h=105, l=99, c=104))
        if second:
            bars.append(_bar(21, o=104, h=106, l=103, c=105))
    else:
        bars.append(_bar(20, o=100, h=101, l=95, c=96))
        if second:
            bars.append(_bar(21, o=96, h=97, l=94, c=95))
    return bars


def _p(**changes):
    return replace(AnchorParameters(
        consolidation_windows=(5, 7),
        er_balance=0.25,
        range_balance_atr=2.0,
        breakout_buffer_atr=0.5,
        confirm_bars=2,
        reclaim_buffer_atr=0.5,
    ), **changes)


def _run(bars=None, *, p=None, t0=None):
    if bars is None:
        bars = _series()
    if t0 is None:
        t0 = bars[-1].available_at
    return scan_m1_consolidation_anchors(
        bars, secid="SBER", timeframe="H1", t0=t0, parameters=p or _p(),
    )


def test_up_anchor_requires_two_observed_outside_closes():
    bars = _series("UP")
    result = _run(bars)
    assert result["status"] == "CONFIRMED_RESEARCH_ONLY"
    a = result["confirmed_anchor"]
    assert a["direction"] == "UP"
    assert a["breakout_index"] == 20
    assert a["consolidation"]["upper"] == 101
    assert a["consolidation"]["lower"] == 99
    assert a["consolidation"]["length"] == 7
    assert a["event_time"] == bars[20].end
    assert a["confirmed_at"] == bars[21].end
    assert a["available_at"] == bars[21].available_at
    assert result["forecast_allowed"] is False
    assert result["calendar_attested"] is False


def test_down_anchor_mirrors_up():
    result = _run(_series("DOWN"))
    assert result["status"] == "CONFIRMED_RESEARCH_ONLY"
    assert result["confirmed_anchor"]["direction"] == "DOWN"
    assert result["confirmed_anchor"]["consolidation"]["lower"] == 99


def test_first_outside_close_is_pending_not_confirmed():
    result = _run(_series(second=False))
    assert result["status"] == "PENDING"
    assert result["confirmed_anchor"] is None
    assert result["pending_candidate"]["next_required_index"] == 21
    assert result["pending_candidate"]["confirmed_at"] is None


def test_t0_prefix_invariance_against_later_reclaim():
    bars = _series()
    t0 = bars[-1].available_at
    later = _bar(22, o=105, h=106, l=97, c=98)
    assert _run(bars, t0=t0) == _run(bars + [later], t0=t0)


def test_reclaim_after_confirmation_cancels_anchor_at_later_t0():
    bars = _series()
    assert _run(bars)["confirmed_anchor"] is not None
    bars.append(_bar(22, o=105, h=106, l=97, c=98))
    result = _run(bars)
    assert result["status"] == "NONE"
    assert result["confirmed_anchor"] is None


def test_neutral_close_breaks_consecutive_confirmation_even_without_reclaim():
    bars = _series()
    bars[21] = _bar(21, o=104, h=105, l=100, c=100.5)
    assert _run(bars)["confirmed_anchor"] is None
    assert _run(bars)["status"] == "NONE"


def test_strict_breakout_boundary_equality_does_not_start_anchor():
    bars = _series(second=False)
    bars[20] = _bar(20, o=100, h=103, l=99, c=102)
    # upper=101, buffer=0.5 * frozen ATR(2)=1 => 102 exactly
    assert _run(bars)["status"] == "NONE"


def test_prebreakout_atr_is_frozen_not_contaminated_by_breakout_bar():
    bars = _series()
    result = _run(bars)
    assert result["confirmed_anchor"]["consolidation"]["atr_frozen"] == 2.0


def test_delayed_second_receipt_is_unavailable_not_confirmed():
    bars = _series()
    t0 = bars[-1].available_at
    bars[-1] = replace(bars[-1], available_at=t0 + timedelta(seconds=1))
    result = _run(bars, t0=t0)
    assert result["status"] == "INPUT_UNAVAILABLE"
    assert result["confirmed_anchor"] is None


def test_missing_middle_bar_blocks_without_compressing_history():
    bars = _series()
    bars[18] = replace(bars[18], source_ref=None)
    result = _run(bars)
    assert result["status"] == "INPUT_UNAVAILABLE"
    assert result["accepted_bars"] == 18


def test_incomplete_bar_blocks_and_cannot_become_a_future_confirmation():
    bars = _series()
    bars[21] = replace(bars[21], is_complete=False)
    assert _run(bars)["status"] == "INPUT_UNAVAILABLE"


def test_window_tiebreak_prefers_longest_valid_consolidation():
    a = _run()["confirmed_anchor"]
    assert a["consolidation"]["start_index"] == 13
    assert a["consolidation"]["end_index"] == 19


def test_warmup_does_not_invent_atr_or_anchor():
    result = _run([_bar(i) for i in range(14)])
    assert result["status"] == "WARMUP"
    assert result["confirmed_anchor"] is None


def test_zero_atr_cannot_generate_divide_by_zero_anchor():
    bars = [_bar(i, o=100, h=100, l=100, c=100) for i in range(20)]
    assert _run(bars)["confirmed_anchor"] is None


def test_trending_windows_do_not_pass_balance_er():
    bars = [_bar(i, o=100 + i, c=100 + i, h=101 + i, l=99 + i) for i in range(20)]
    bars.append(_bar(20, o=120, c=124, h=125, l=119))
    assert _run(bars)["confirmed_anchor"] is None


def test_mixed_future_contracts_raise_instead_of_join():
    bars = _series()
    bars[5] = replace(bars[5], secid="SiZ6")
    with pytest.raises(ValueError, match="contract"):
        _run(bars)


def test_violated_ohlc_envelope_rejected():
    bars = _series()
    bars[3] = replace(bars[3], close=1000)
    with pytest.raises(ValueError, match="inconsistent OHLC"):
        _run(bars)


def test_bad_settings_refused_without_guessing_defaults():
    for overrides in (
        {"consolidation_windows": (5, 5)},
        {"consolidation_windows": ()},
        {"confirm_bars": 0},
        {"er_balance": float("nan")},
        {"er_balance": 2.0},
        {"breakout_buffer_atr": -1},
        {"reclaim_buffer_atr": True},
    ):
        with pytest.raises(ValueError):
            _run(p=_p(**overrides))


def test_same_inputs_repeat_identically():
    assert _run() == _run()


def test_confirm_bars_one_requires_only_the_breakout_bar():
    result = _run(_series(second=False), p=_p(confirm_bars=1))
    assert result["status"] == "CONFIRMED_RESEARCH_ONLY"
    assert result["confirmed_anchor"]["confirmed_at"] == _series(second=False)[-1].end


def test_reclaim_at_frozen_threshold_equality_cancels_candidate():
    bars = _series()
    # upper=101; reclaim buffer 0.5 * ATR(2), threshold=100 exactly.
    bars.append(_bar(22, o=105, h=106, l=99, c=100))
    assert _run(bars)["confirmed_anchor"] is None
