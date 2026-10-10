"""Offline, no-network research D1 feature formula and admission tests."""
from __future__ import annotations

from dataclasses import replace

import pytest

from birzha.application.d1_research_features import (
    D1_RESEARCH_FEATURE_VERSION, build_d1_research_features,
)
from birzha.application.features import TimeframeFeatureEngine
from birzha.domain.market import Candle, CandleSeries, Instrument


def _series(count=21, *, asset_class="future", capabilities=("CANDLES","VOLUME")):
    secid = "BRM5" if asset_class == "future" else "IMOEX"
    instrument = Instrument(
        symbol="BR" if asset_class == "future" else "IMOEX",
        secid=secid, root_symbol="BR" if asset_class == "future" else None,
        board="RFUD" if asset_class == "future" else "SNDX",
        engine="futures" if asset_class == "future" else "stock",
        market="forts" if asset_class == "future" else "index",
        asset_class=asset_class,
        data_capabilities=capabilities,
    )
    # Strictly consecutive *provided* sessions, not inferred exchange sessions.
    from datetime import date, timedelta
    days = tuple((date(2025,1,1)+timedelta(days=i)).isoformat() for i in range(count))
    candles = tuple(
        Candle(open=101.0+i, close=101.0+i,
               high=103.0+i, low=99.0+i,
               volume=10.0+i if "VOLUME" in capabilities else 0.0,
               value=1000.0+i,
               begin=f"{day}T10:00:00", end=f"{day}T23:49:59",
               completed=True, source="MOEX_ISS")
        for i,day in enumerate(days)
    )
    return CandleSeries(instrument,"D1",candles),days


def _calc(series,dates):
    return build_d1_research_features(
        series,exact_secid=series.instrument.secid,expected_sessions=dates,
        evidence_origin="RECONSTRUCTED_MOEX",evidence_key="synthetic-fixture",
    ).to_dict()


def _f(data,name):
    return data["features"][name]


def test_exact_21_session_sma_true_range_and_direct_displacement():
    series,dates=_series()
    r=_calc(series,dates)
    assert r["schema"]==D1_RESEARCH_FEATURE_VERSION
    assert r["atr_method"]=="SMA_14_TRUE_RANGES_NOT_WILDER"
    assert r["strict_historical_as_known_at_t0"] is False
    assert _f(r,"atr14_sma_tr")=={"value":4.0,"status":"AVAILABLE","reason":None}
    assert _f(r,"d20_atr")["value"]==5.0
    assert _f(r,"d5_atr")["value"]==1.25
    assert _f(r,"er20")["value"]==1.0
    assert _f(r,"w20_atr")["value"]==pytest.approx(23.0/4.0)
    assert _f(r,"sma20")["value"]==pytest.approx(sum(range(102,122))/20)
    assert _f(r,"sma50")["status"]=="INSUFFICIENT_HISTORY"
    # Existing normalized fraction of return divided by fraction ATR is
    # not the identical direct-displacement quantity.
    baseline=TimeframeFeatureEngine().build(series)
    assert baseline.return_20 / baseline.atr_14_pct != _f(r,"d20_atr")["value"]
    assert _f(r,"volume_ratio20")["status"]=="AVAILABLE"


def test_flat_close_path_reports_distinct_unavailable_not_fake_zero():
    series,days=_series()
    bars=tuple(replace(b, open=100.0,close=100.0,high=102.0,low=98.0) for b in series.candles)
    r=_calc(replace(series,candles=bars),days)
    assert _f(r,"er20")=={"value":None,"status":"UNAVAILABLE","reason":"zero_20_step_closing_path"}
    assert _f(r,"d20_atr")["value"]==0.0


def test_exact_session_gap_or_reordered_duplicate_is_rejected():
    series,dates=_series()
    with pytest.raises(ValueError,match="session mismatch"):
        _calc(series,dates[:-1])
    with pytest.raises(ValueError,match="duplicate"):
        _calc(series,dates[:-1]+(dates[-2],))
    with pytest.raises(ValueError,match="candle session mismatch"):
        _calc(replace(series,candles=series.candles[:-2]+(series.candles[-1],)),dates[:-1])
    with pytest.raises(ValueError,match="exact SECID"):
        build_d1_research_features(series, exact_secid="BRU5",
            expected_sessions=dates,evidence_origin="RECONSTRUCTED_MOEX",
            evidence_key="synthetic")


def test_missing_ohlc_and_unfinished_are_unavailable_not_compressed():
    series,days=_series()
    for changed in (replace(series.candles[7],close=None),
                    replace(series.candles[7],completed=False),
                    replace(series.candles[7],high=float("nan"))):
        rows=list(series.candles)
        rows[7]=changed
        r=_calc(replace(series,candles=tuple(rows)),days)
        assert _f(r,"d20_atr")["status"]=="UNAVAILABLE"
        assert _f(r,"w20_atr")["status"]=="UNAVAILABLE"


def test_index_zero_volume_is_not_a_missing_d1_price():
    series,days=_series(asset_class="index",capabilities=("CANDLES","TRADING_CALENDAR"))
    r=_calc(series,days)
    assert _f(r,"d20_atr")["status"]=="AVAILABLE"
    assert _f(r,"volume_ratio20")["status"]=="NOT_APPLICABLE"
    assert _f(r,"oi_change_ratio")["status"]=="NOT_APPLICABLE"
    assert _f(r,"session_vwap")["status"]=="NOT_APPLICABLE"
    assert _f(r,"profile_poc")["status"]=="NOT_APPLICABLE"


def test_supported_but_absent_profile_oi_and_tradestats_are_unavailable():
    series,days=_series(capabilities=("CANDLES","VOLUME","TRADESTATS","OPEN_INTEREST"))
    r=_calc(series,days)
    for field in ("oi_change_ratio","session_vwap","profile_poc"):
        assert _f(r,field)["value"] is None
        assert _f(r,field)["status"]=="UNAVAILABLE"


def test_short_window_does_not_assert_sma50_or_d20():
    series,days=_series(count=20)
    r=_calc(series,days)
    assert _f(r,"sma50")["status"]=="INSUFFICIENT_HISTORY"
    assert _f(r,"d20_atr")["status"]=="INSUFFICIENT_HISTORY"
    assert _f(r,"w20_atr")["status"]=="AVAILABLE"


def test_zero_true_range_blocks_atr_normalization_not_sma():
    series,days=_series()
    flat=tuple(replace(x,open=100.0,close=100.0,low=100.0,high=100.0) for x in series.candles)
    r=_calc(replace(series,candles=flat),days)
    assert _f(r,"atr14_sma_tr")["reason"]=="zero_or_negative_atr14"
    assert _f(r,"d20_atr")["value"] is None
    assert _f(r,"w20_atr")["value"] is None
    assert _f(r,"sma20")["value"]==100.0


def test_formula_hash_determinism_and_no_forecast_side_effects():
    series,days=_series(count=50)
    a=_calc(series,days)
    b=_calc(series,days)
    assert a==b
    assert _f(a,"sma50")["status"]=="AVAILABLE"
    assert a["observed_bars"]==50
