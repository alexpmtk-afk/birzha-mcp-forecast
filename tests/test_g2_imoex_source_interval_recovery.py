from datetime import datetime,timedelta

import pytest

from birzha.domain.market import Candle
from scripts.g2_imoex_source_interval_recovery import pack,same_price


def sample():
    start=datetime.fromisoformat("2025-06-09T10:00:00+03:00")
    return Candle(
        open=3000.,high=3010.,low=2990.,close=3005.,
        volume=3.0,value=9000.0,
        begin=start.isoformat(),end=(start+timedelta(minutes=7,seconds=59)).isoformat(),
        completed=True,
    )


def test_short_native_H1_is_stored_with_original_range_not_faked_full_hour():
    c=sample()
    row=pack("H1",c,"NATIVE_H1_SHORT_OBSERVED",None)
    assert row["end"].endswith("10:07:59+03:00")
    assert row["source_minute_rows"] is None
    assert row["synthetic_price"] is False


def test_partial_M15_preserves_native_OHLC_and_evidence_count():
    c=sample()
    row=pack("M15",c,"SOURCE_M15_WITH_FEWER_THAN_15_M1",8)
    assert row["open"]==3000 and row["close"]==3005
    assert row["volume"]==3
    assert row["source_minute_rows"]==8


def test_original_price_mismatch_cannot_be_silently_accepted():
    c=sample()
    record={"begin":c.begin,"end_time":c.end,
            "open":c.open,"high":c.high,"low":c.low,
            "close":c.close,"value":c.value,"volume":c.volume}
    assert same_price(record,c)
    record["close"]=c.close+1
    assert not same_price(record,c)


def test_refuse_invalid_source_ohlc_or_forbidden_year():
    with pytest.raises(ValueError):
        pack("M15",sample().__class__(
            open=-1., high=3010., low=2990.,close=3005.,
            value=9000.,volume=3.,
            begin="2025-06-09T10:00:00+03:00",
            end="2025-06-09T10:14:59+03:00"),
            "SOURCE_M15_WITH_FEWER_THAN_15_M1",8)
    c=sample()
    with pytest.raises(ValueError,match="2025-01-01..2026-12-31"):
        pack("H1",c.__class__(open=3000.,high=3010.,low=2990.,close=3005.,
             value=9000.,volume=3.,begin="2024-06-09T10:00:00+03:00",
             end="2024-06-09T10:07:59+03:00"),"NATIVE_H1_SHORT_OBSERVED",None)
