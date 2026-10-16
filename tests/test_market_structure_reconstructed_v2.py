"""No future data, no artificial completion and no silent ATR unit changes."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import pytest

from birzha.application.market_structure_facts import (
    VERSION, build_market_structure_facts,
    build_reconstructed_market_structure_facts,
)
from birzha.domain.market import Instrument, CandleSeries, Candle

TZ=timezone(timedelta(hours=3))
T0="2025-06-30T23:59:59+03:00"
LABEL="Europe/Moscow UTC+03:00 2025-2026 (assumption, not source receipt)"
SHA="a"*64


def _rows(count=60, bad=False):
    origin=datetime(2025,4,1,tzinfo=TZ)
    bars=[]
    for i in range(count):
        start=origin+timedelta(days=i)
        close=100.0
        bars.append(Candle(open=close,high=102.0,low=98.0,close=close,
                           value=None,volume=10.0,begin=start.isoformat(),
                           end=(start+timedelta(hours=1)-timedelta(seconds=1)).isoformat(),
                           completed=False if i==5 and bad else True,
                           source="RECONSTRUCTED_RESEARCH_ONLY"))
    inst=Instrument(symbol="SBER",secid="SBER",board="TQBR",engine="stock",
        market="shares",asset_class="equity",data_capabilities=("CANDLES","VOLUME"))
    return CandleSeries(instrument=inst,timeframe="H1",candles=tuple(bars),
                        source="RECONSTRUCTED_RESEARCH_ONLY")


def test_strict_entry_does_not_relax_forming_bar_rule():
    series=_rows(bad=True)
    with pytest.raises(ValueError,match="forming candle not admitted"):
        build_market_structure_facts(series,t0=T0)


def test_explicit_reconstructed_entry_reuses_wilder_and_keeps_false_bit():
    series=_rows(bad=True)
    frozen=json.dumps(series.to_dict(),sort_keys=True)
    r=build_reconstructed_market_structure_facts(series,t0=T0,
                            timezone_assumption=LABEL,original_input_sha256=SHA)
    assert json.dumps(series.to_dict(),sort_keys=True) == frozen
    assert series.candles[5].completed is False
    assert series.candles[5].available_at is None
    assert r["schema"]==VERSION
    assert r["original_completed_false"]==1
    assert r["missing_first_receipt_metadata_bars"]==60
    assert r["facts"]["wilder_atr14"]["value"]["atr_price"]==4.0
    assert r["facts"]["wilder_atr14"]["value"]["atr_pct"]==4.0
    assert r["facts"]["wilder_atr14"]["value"]["atr_ratio"]==0.04
    assert r["facts"]["relative_volume_prior20"]["value"]["ratio_to_prior_20_median"]==1.0
    assert r["forecast_or_trading_authorized"] is False
    assert r["independent_first_receipt_attested"] is False
    assert r["source_session_eligibility_attested"] is False


@pytest.mark.parametrize("count",[0,16,49])
def test_no_fabricated_bars_when_50_are_missing(count):
    s=_rows(count=count)
    r=build_reconstructed_market_structure_facts(s,t0=T0,
                              timezone_assumption=LABEL,original_input_sha256=SHA)
    assert r["total_input_bars"]==count
    assert r["facts"]["wilder_atr14"]["status"]=="INSUFFICIENT_HISTORY"
    assert r["facts"]["confirmed_price_structure"]["status"]=="INSUFFICIENT_HISTORY"


def test_future_candle_refused_even_in_research():
    s=_rows()
    bad=replace(s,candles=s.candles+(replace(s.candles[-1],
       begin="2025-07-01T00:00:00+03:00",end="2025-07-01T00:59:59+03:00"),))
    with pytest.raises(ValueError,match="future/invalid"):
        build_reconstructed_market_structure_facts(bad,t0=T0,
                             timezone_assumption=LABEL,original_input_sha256=SHA)


def test_original_available_at_not_backfilled_or_falsely_attested():
    s=_rows()
    future=replace(s.candles[0],observed_at="2026-10-17T00:00:00+03:00")
    with pytest.raises(ValueError,match="future observed_at"):
        build_reconstructed_market_structure_facts(
            replace(s,candles=(future,)+s.candles[1:]),t0=T0,
            timezone_assumption=LABEL,original_input_sha256=SHA)


def test_source_timezone_and_sha_controls_are_required():
    s=_rows()
    for kwargs in [
        {"timezone_assumption":"UTC"},
        {"original_input_sha256":"fake"},
    ]:
        opts={"timezone_assumption":LABEL,"original_input_sha256":SHA}
        opts.update(kwargs)
        with pytest.raises(ValueError):
            build_reconstructed_market_structure_facts(s,t0=T0,**opts)
    with pytest.raises(ValueError,match="reconstructed"):
        build_reconstructed_market_structure_facts(
            replace(s,source="MOEX_ISS"),t0=T0,
            timezone_assumption=LABEL,original_input_sha256=SHA)


def test_shaped_errors_unchanged_by_false_completed_flag():
    s=_rows(bad=True)
    bad=replace(s,candles=s.candles[:10]+(replace(s.candles[10],high=99.0),)+s.candles[11:])
    with pytest.raises(ValueError,match="inconsistent OHLC"):
        build_reconstructed_market_structure_facts(bad,t0=T0,
                             timezone_assumption=LABEL,original_input_sha256=SHA)


def test_driver_assumption_parse_fails_on_aware_source_timestamp():
    from scripts.g2_market_structure_archive_direct import _archive_stamp
    assert _archive_stamp("2025-03-31 23:59:41").endswith("+03:00")
    with pytest.raises(ValueError,match="frozen naive"):
        _archive_stamp("2025-03-31T23:59:41+03:00")


def test_primary_strict_d1_is_distinct_from_reconstructed_h1():
    s=_rows()
    d1=replace(s,timeframe="D1")
    got=build_market_structure_facts(d1,t0=T0)
    assert got["facts"]["wilder_atr14"]["value"]["atr_pct"]==4.0
    assert got["forecast_or_trading_authorized"] is False
