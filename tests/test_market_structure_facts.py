"""Non-predictive, T0-safe Market Structure facts acceptance cases."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import math

import pytest

from birzha.application.market_structure_facts import (
    VERSION, ARCHIVED_SOURCE, build_market_structure_facts,
    build_archived_h1_descriptive_facts,
)
from birzha.domain.market import Candle, CandleSeries, Instrument


def history(*, count=60, slope=0.2, volume=True, varying=True):
    inst = Instrument(
        symbol="SBER", secid="SBER", board="TQBR", engine="stock",
        market="shares", asset_class="equity",
        data_capabilities=("CANDLES", "VOLUME") if volume else ("CANDLES",),
    )
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    bars = []
    for i in range(count):
        begin = start + timedelta(days=i)
        end = begin + timedelta(days=1, seconds=-1)
        price = (100+slope*i+5*math.sin(i*math.pi/4)) if varying else 100
        bars.append(Candle(
            open=price, close=price, high=price+2, low=price-2,
            value=None, volume=30 if i == count-1 else 10,
            begin=begin.isoformat(), end=end.isoformat(), completed=True,
            available_at=end.isoformat(), observed_at=end.isoformat(),
            available_at_confidence="EXACT", source="MOEX_ISS",
        ))
    return CandleSeries(inst, "D1", tuple(bars)), bars[-1].end


def replace_bar(series, index, **changes):
    bars = list(series.candles)
    bars[index] = replace(bars[index], **changes)
    return replace(series, candles=tuple(bars))


@pytest.mark.parametrize("slope,expected",[(0.2, "HH_HL"),(-0.2, "LH_LL")])
def test_confirmed_swing_structure_is_descriptive_only(slope, expected):
    series, t0 = history(slope=slope)
    got = build_market_structure_facts(series, t0=t0)
    assert got["schema"] == VERSION
    assert got["facts"]["confirmed_price_structure"]["status"] == "AVAILABLE"
    assert got["facts"]["confirmed_price_structure"]["value"]["structure"] == expected
    assert got["directional_prediction"] is None
    assert got["market_state_regime"] is None
    assert got["control"] == "UNKNOWN"
    assert got["forecast_or_trading_authorized"] is False
    assert got["independent_first_receipt_attested"] is False


def test_wilder_atr_true_range_and_prior_volume_median():
    series, t0 = history(varying=False)
    got = build_market_structure_facts(series, t0=t0)["facts"]
    assert got["wilder_atr14"]["status"] == "AVAILABLE"
    assert got["wilder_atr14"]["value"]["atr_price"] == 4.0
    assert got["wilder_atr14"]["value"]["atr_pct"] == 4.0
    assert got["wilder_atr14"]["value"]["atr_ratio"] == 0.04
    assert got["wilder_atr14"]["value"]["atr_pct_unit"] == "PERCENT_100X_RATIO"
    assert got["relative_volume_prior20"]["value"]["ratio_to_prior_20_median"] == 3.0
    assert got["relative_volume_prior20"]["value"]["prior_20_median_volume"] == 10


def test_volume_missing_capability_is_not_zero():
    s,t = history(volume=False)
    result = build_market_structure_facts(s,t0=t)["facts"]["relative_volume_prior20"]
    assert result["status"] == "NOT_APPLICABLE" and result["value"] is None


def test_first_candle_missing_ohlc_in_fixed_window_does_not_compress():
    s,t = history(count=60)
    s = replace_bar(s,15, high=None)
    facts = build_market_structure_facts(s,t0=t)["facts"]
    assert facts["wilder_atr14"]["status"] == "UNAVAILABLE"
    assert facts["confirmed_price_structure"]["status"] == "UNAVAILABLE"
    assert facts["wilder_atr14"]["value"] is None


@pytest.mark.parametrize("count",[0,14,20,49])
def test_incomplete_history_has_explicit_absence_and_no_label(count):
    s,t = history(count=60)
    s = replace(s,candles=s.candles[:count])
    got = build_market_structure_facts(s,t0=t)
    assert got["facts"]["wilder_atr14"]["status"] == "INSUFFICIENT_HISTORY"
    assert got["facts"]["confirmed_price_structure"]["status"] == "INSUFFICIENT_HISTORY"
    assert got["directional_prediction"] is None


def test_no_future_bar_can_confirm_previous_swing():
    s,t=history()
    previous=s.candles[-2].end
    with pytest.raises(ValueError, match="future candle"):
        build_market_structure_facts(s,t0=previous)


@pytest.mark.parametrize("change", [
    {"completed":False},
    {"available_at":"2027-01-01T00:00:00+00:00"},
    {"observed_at":"2027-01-01T00:00:00+00:00"},
    {"close":float("nan")},
    {"open":True},
    {"high":1.0},
])
def test_corrupted_or_unavailable_candle_is_rejected(change):
    s,t=history()
    s=replace_bar(s,-1,**change)
    with pytest.raises(ValueError):
        build_market_structure_facts(s,t0=t)


def test_t0_metadata_gate_does_not_claim_independent_first_receipt():
    s,t=history()
    got=build_market_structure_facts(s,t0=t,mode="T0_METADATA_GATED")
    assert got["mode"] == "T0_METADATA_GATED"
    assert got["independent_first_receipt_attested"] is False


@pytest.mark.parametrize("changes", [
    {"observed_at": None},
    {"available_at": None},
    {"available_at_confidence":"UNKNOWN"},
    {"source":"NORMALIZED_RECONSTRUCTED_BUNDLE"},
])
def test_strict_claimed_metadata_fails_closed(changes):
    s,t=history()
    s=replace_bar(s,3,**changes)
    with pytest.raises(ValueError,match="T0 metadata|reconstructed"):
        build_market_structure_facts(s,t0=t,mode="T0_METADATA_GATED")
    # Reconstructed work never turns those missing fields into synthetic T0 proof.
    result=build_market_structure_facts(s,t0=t,mode="RECONSTRUCTED_RESEARCH_ONLY")
    assert result["independent_first_receipt_attested"] is False


def test_tie_is_not_invented_pivot():
    s,t=history(varying=False)
    fact=build_market_structure_facts(s,t0=t)["facts"]["confirmed_price_structure"]
    assert fact["status"] == "INSUFFICIENT_HISTORY"
    assert fact["value"] is None


def test_deterministic_input_hash_and_missing_past_is_not_a_later_window():
    s,t=history()
    first=build_market_structure_facts(s,t0=t)
    assert first==build_market_structure_facts(s,t0=t)
    changed=replace_bar(s,0,volume=12)
    assert first["data_sha256"] != build_market_structure_facts(changed,t0=t)["data_sha256"]


def test_wrong_timeframe_or_missing_timezone_rejected():
    s,t=history()
    with pytest.raises(ValueError):
        build_market_structure_facts(replace(s,timeframe="M15"),t0=t)
    with pytest.raises(ValueError,match="timezone"):
        build_market_structure_facts(s,t0="2026-10-16T12:00:00")


def test_overlapping_or_reordered_bars_rejected():
    s,t=history()
    s=replace_bar(s,1,begin=s.candles[0].begin)
    with pytest.raises(ValueError,match="overlapping"):
        build_market_structure_facts(s,t0=t)



def _archived_h1():
    s,t = history()
    from dataclasses import replace
    from datetime import timedelta
    bars = []
    for idx,c in enumerate(s.candles):
        start = datetime.fromisoformat(c.begin)
        # An archived short interval is flagged False by an obsolete rule.
        end = (start + timedelta(minutes=54)).isoformat() if idx == 30 else c.end
        bars.append(replace(c, begin=c.begin, end=end,
                            completed=False if idx == 30 else True,
                            available_at=None, observed_at=None,
                            available_at_confidence="UNKNOWN", source=ARCHIVED_SOURCE))
    return replace(s, timeframe="H1", candles=tuple(bars), source=ARCHIVED_SOURCE), t


def test_archived_h1_is_separate_and_does_not_promote_old_false_flags():
    s,t = _archived_h1()
    with pytest.raises(ValueError, match="forming candle"):
        build_market_structure_facts(s, t0=t, mode="RECONSTRUCTED_RESEARCH_ONLY")
    with pytest.raises(ValueError, match="forming candle"):
        build_market_structure_facts(s, t0=t, mode="T0_METADATA_GATED")
    output=build_archived_h1_descriptive_facts(s, t0=t)
    assert output["duration_flagged_bars"] == 1
    assert output["total_completed_bars"] == len(s.candles)-1
    assert output["total_input_bars"] == len(s.candles)
    assert output["archived_completed_flags_preserved"] is True
    assert output["original_completion_unknown_for_duration_flags"] is True
    assert output["strict_pit_admitted"] is False
    assert output["forecast_or_trading_authorized"] is False
    assert output["facts"]["wilder_atr14"]["status"] == "AVAILABLE"
    assert s.candles[30].completed is False


def test_archive_still_refuses_future_bar_bad_provenance_and_availability():
    s,t = _archived_h1()
    s_bad=replace_bar(s,30,source="MISLABELED")
    with pytest.raises(ValueError, match="provenance"):
        build_archived_h1_descriptive_facts(s_bad,t0=t)
    s_bad=replace_bar(s,30,completed=True)
    with pytest.raises(ValueError,match="duration flag disagrees"):
        build_archived_h1_descriptive_facts(s_bad,t0=t)
    s_bad=replace_bar(s,30,observed_at=s.candles[30].end)
    with pytest.raises(ValueError,match="no constructed availability"):
        build_archived_h1_descriptive_facts(s_bad,t0=t)
    with pytest.raises(ValueError,match="future candle"):
        build_archived_h1_descriptive_facts(s,t0=s.candles[-2].end)


def test_archived_research_uses_same_formulas_without_candle_drops():
    s,t=_archived_h1()
    from dataclasses import replace
    with_flag=build_archived_h1_descriptive_facts(s,t0=t)
    # Last 50 are all unflagged in this fixture, so changing only the
    # source of the marked earlier candle does not alter last50 formulas.
    alternate=replace(s,candles=tuple(replace(c,source=ARCHIVED_SOURCE) for c in s.candles))
    assert build_archived_h1_descriptive_facts(alternate,t0=t)["facts"]==with_flag["facts"]
    noflags=replace(s,candles=tuple(replace(c, completed=True, source="MOEX_ISS")
                    for c in s.candles),source="MOEX_ISS")
    assert build_market_structure_facts(noflags,t0=t)["facts"]==with_flag["facts"]


def test_arithmetic_module_version_changed_when_atr_pct_unit_changed():
    assert VERSION == "MARKET_STRUCTURE_FACTS_V2_20261017"
