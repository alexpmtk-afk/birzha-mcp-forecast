"""Causal pivot test bars are synthetic; no MOEX data, network or holdout."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from birzha.application.m1_causal_swings import CausalBar, scan_m1_confirmed_swings

START = datetime(2026, 10, 19, 6, tzinfo=timezone.utc)


def _bars(highs=(10, 12, 20, 13, 11), lows=(5, 5, 5, 5, 5)):
    out = []
    for i, (high, low) in enumerate(zip(highs, lows)):
        start = START + timedelta(hours=i)
        end = start + timedelta(hours=1)
        out.append(CausalBar(
            secid="SBER", timeframe="H1", start=start, end=end,
            available_at=end + timedelta(minutes=1),
            open=(high + low) / 2, high=high, low=low,
            close=(high + low) / 2, is_complete=True,
            source_ref=f"synthetic:bar-{i}",
        ))
    return out


def _run(bars=None, *, t0=None, swing_w=2, tie_break="EARLIEST"):
    actual = _bars() if bars is None else bars
    if t0 is None:
        t0 = actual[-1].end + timedelta(minutes=1)
    return scan_m1_confirmed_swings(
        actual, secid="SBER", timeframe="H1", t0=t0,
        swing_w=swing_w, tie_break=tie_break,
    )


def test_swing_requires_full_right_window():
    bars = _bars()
    early = _run(bars, t0=bars[3].available_at)
    mature = _run(bars, t0=bars[4].available_at)
    assert early["swings"] == []
    assert early["blocked_windows"] == 1
    assert len(mature["swings"]) == 1
    assert mature["swings"][0]["side"] == "HIGH"


def test_event_and_confirmation_times_are_distinct():
    bars = _bars()
    s = _run(bars)["swings"][0]
    assert s["event_start_time"] == bars[2].start
    assert s["event_time"] == bars[2].end
    assert s["event_time_precision"] == "BAR_END_PROXY"
    assert s["confirmed_at"] == bars[4].end
    assert s["available_at"] == bars[4].available_at
    assert s["source_refs"] == tuple(b.source_ref for b in bars)


def test_future_appended_bars_cannot_change_past_confirmed_pivots():
    bars = _bars()
    t0 = bars[-1].available_at
    future = replace(
        bars[-1], start=bars[-1].end, end=bars[-1].end + timedelta(hours=1),
        available_at=bars[-1].end + timedelta(hours=1, minutes=1),
        high=200, low=3, open=100, close=100, source_ref="synthetic:future",
    )
    assert _run(bars, t0=t0)["swings"] == _run(bars + [future], t0=t0)["swings"]


def test_delayed_original_receipt_blocks_confirmation():
    bars = _bars()
    t0 = bars[-1].available_at
    bars[4] = replace(bars[4], available_at=t0 + timedelta(minutes=1))
    assert _run(bars, t0=t0)["swings"] == []
    assert _run(bars, t0=t0)["blocked_windows"] == 1


def test_incomplete_right_bar_does_not_disappear_from_window():
    bars = _bars()
    bars[-1] = replace(bars[-1], is_complete=False)
    assert _run(bars)["swings"] == []


def test_missing_availability_blocks_window():
    bars = _bars()
    bars[-1] = replace(bars[-1], available_at=None)
    assert _run(bars)["swings"] == []


def test_latest_tie_high_selects_center_but_earliest_does_not():
    bars = _bars(highs=(10, 20, 20, 13, 11))
    assert _run(bars, tie_break="EARLIEST")["swings"] == []
    assert [s["side"] for s in _run(bars, tie_break="LATEST")["swings"]] == ["HIGH"]


def test_latest_tie_low_selects_center():
    bars = _bars(highs=(10, 10, 10, 10, 10), lows=(5, 1, 1, 4, 5))
    assert _run(bars, tie_break="EARLIEST")["swings"] == []
    assert [s["side"] for s in _run(bars, tie_break="LATEST")["swings"]] == ["LOW"]


def test_fully_flat_series_does_not_create_two_spurious_swings():
    assert _run(_bars(highs=(10,)*5, lows=(10,)*5))["swings"] == []


def test_mixed_secid_cannot_cross_contract_boundary():
    bars = _bars()
    bars[2] = replace(bars[2], secid="SiZ6")
    with pytest.raises(ValueError, match="contract/timeframe mismatch"):
        _run(bars)


def test_out_of_order_or_overlapping_bars_rejected():
    bars = _bars()
    bars[1] = replace(bars[1], start=bars[0].start)
    with pytest.raises(ValueError, match="overlapping"):
        _run(bars)


def test_ohlc_outside_envelope_rejected():
    bars = _bars()
    bars[1] = replace(bars[1], close=1000)
    with pytest.raises(ValueError, match="invalid OHLC"):
        _run(bars)


def test_nonfinite_price_rejected():
    bars = _bars()
    bars[1] = replace(bars[1], high=float("nan"))
    with pytest.raises(ValueError, match="nonfinite"):
        _run(bars)


def test_availability_cannot_precede_close():
    bars = _bars()
    bars[0] = replace(bars[0], available_at=bars[0].start)
    with pytest.raises(ValueError, match="availability"):
        _run(bars)


def test_naive_t0_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _run(t0=START.replace(tzinfo=None))


def test_parameter_guessing_forbidden():
    for value in (0, -1, 2.5, True):
        with pytest.raises(ValueError, match="swing_w"):
            _run(swing_w=value)
    with pytest.raises(ValueError, match="tie_break"):
        _run(tie_break="AUTO")


def test_invalid_completion_flag_rejected():
    bars = _bars()
    bars[4] = replace(bars[4], is_complete="yes")
    with pytest.raises(ValueError, match="completion flag"):
        _run(bars)


def test_missing_source_reference_blocks_window():
    bars = _bars()
    bars[4] = replace(bars[4], source_ref="  ")
    assert _run(bars)["swings"] == []


def test_repeated_identical_input_produces_identical_output():
    assert _run() == _run()


def test_short_series_requests_warmup_not_a_fake_pivot():
    b = _bars()[:3]
    result = _run(b)
    assert result["swings"] == []
    assert result["total_complete_windows"] == 0
    assert result["forecast_allowed"] is False


def test_invalid_source_reference_type_rejected():
    bars = _bars()
    bars[3] = replace(bars[3], source_ref=123)
    with pytest.raises(ValueError, match="source reference"):
        _run(bars)
