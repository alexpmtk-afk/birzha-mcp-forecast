from datetime import datetime,timedelta

import pytest

from birzha.application.research_intraday_archive import ResearchIntradayArchive
from birzha.application.research_intraday_source_compat import IMOEXResearchPriceView
from birzha.domain.market import Candle,CandleSeries,Instrument


def inst():
    return Instrument(symbol="IMOEX",secid="IMOEX",board="SNDX",
                      engine="stock",market="index",asset_class="index")


def _bar(start, seconds):
    return Candle(open=3000.,close=3002.,high=3005.,low=2999.,
                  value=3000.,volume=1.,begin=start.isoformat(),
                  end=(start+timedelta(seconds=seconds)).isoformat(),completed=True)


def test_strict_and_opt_in_live_compat_are_separate(tmp_path):
    path=tmp_path/"source_compatible.duckdb"
    store=ResearchIntradayArchive(str(path))
    start=datetime.fromisoformat("2025-06-09T10:00:00+03:00")
    store.ingest(CandleSeries(inst(),"H1",(_bar(start,3599),)))
    store.ingest(CandleSeries(inst(),"M15",(_bar(start,899),)))
    store.conn.execute("""
       CREATE TABLE research_intraday_source_observed_exceptions(
       symbol VARCHAR,secid VARCHAR,timeframe VARCHAR,begin VARCHAR,
       end_time VARCHAR,trade_date VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,
       close DOUBLE,volume DOUBLE,value DOUBLE,quality VARCHAR,
       source_minute_rows INTEGER,source_kind VARCHAR,available_at_confidence VARCHAR)
    """)
    shorter=_bar(start+timedelta(hours=1),450)
    partial=_bar(start+timedelta(minutes=15),899)
    for tf,c,quality,count in (
        ("H1",shorter,"NATIVE_H1_SHORT_OBSERVED",None),
        ("M15",partial,"SOURCE_M15_WITH_FEWER_THAN_15_M1",8),
    ):
        store.conn.execute(
            "INSERT INTO research_intraday_source_observed_exceptions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ["IMOEX","IMOEX",tf,c.begin,c.end,"2025-06-09",
             c.open,c.high,c.low,c.close,c.volume,c.value,quality,count,
             "MOEX_TEST","INFERRED"]
        )
    store.conn.execute("""
        INSERT INTO research_intraday_meta VALUES
        ('source_compatible_exception_extension','MOEX_SOURCE_OBSERVED_V1')
    """)
    store.close()
    view=IMOEXResearchPriceView(path)
    try:
        for tf in ("H1","M15"):
            original=view.read_exact(inst(),timeframe=tf,
                                     from_date="2025-06-09",till_date="2025-06-09")
            compatible=view.read_exact(inst(),timeframe=tf,
                                       from_date="2025-06-09",till_date="2025-06-09",
                                       source_compatible=True)
            assert original.count==1 and compatible.count==2
            assert original.candles[0].source not in ("SOURCE_M15_WITH_FEWER_THAN_15_M1",)
            assert "INFERRED"==compatible.candles[-1].available_at_confidence
        assert view.quality_counts()["M15"]["SOURCE_M15_WITH_FEWER_THAN_15_M1"]==1
    finally:
        view.close()


def test_non_v2_research_database_is_rejected(tmp_path):
    path=tmp_path/"source_only.duckdb"
    store=ResearchIntradayArchive(str(path))
    store.close()
    with pytest.raises(Exception):
        IMOEXResearchPriceView(path)
