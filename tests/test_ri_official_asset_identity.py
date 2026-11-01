from datetime import date
import pytest
from birzha.providers.moex_history import MoexHistoricalFutureResolver
from birzha.providers.continuous_futures import select_completed_session_volume
from test_causal_futures_runtime import row, _Client

@pytest.mark.parametrize("secid,expiry",[("RIH6","2026-03-19"),("RIM6","2026-06-18"),("RIU6","2026-09-17"),("RIZ6","2026-12-17"),("RIH7","2027-03-18")])
def test_official_rts_asset_is_ri_without_relabelling_source(secid,expiry):
    source=row("2026-01-14",secid,"RTS",100,expiry)
    client=_Client({"2026-01-14":[source]})
    original=client._request
    def request(path,params):
        assert params['assetcode']=='RTS'
        assert params['date']<'2026-01-15'
        return original(path,params)
    client._request=request
    ins=MoexHistoricalFutureResolver(client).resolve('RI',date(2026,1,15))
    assert ins.secid==secid and ins.root_symbol==ins.symbol=='RI'
    assert source['ASSETCODE']=='RTS'

def test_shared_selector_uses_previous_rts_volume_not_next_contract_presence():
    rows=[row('2026-10-28','RIZ6','RTS',60019,'2026-12-17'),row('2026-10-28','RIH7','RTS',462,'2027-03-18')]
    result=select_completed_session_volume('RI',decision_session=date(2026,10,28),effective_session=date(2026,10,29),decision_rows=rows)
    assert result.secid=='RIZ6'


def test_official_zero_trades_skips_null_volume_without_fabricating_zero():
    active=row('2026-01-14','RIH6','RTS',67057,'2026-03-19')
    inactive={**row('2026-01-14','RIM7','RTS',None,'2027-06-17'),'NUMTRADES':0}
    decision=select_completed_session_volume('RI',decision_session=date(2026,1,14),effective_session=date(2026,1,15),decision_rows=[active,inactive])
    assert decision.secid=='RIH6' and inactive['VOLUME'] is None

@pytest.mark.parametrize('trades',[None,1,True])
def test_unknown_volume_of_potentially_traded_candidate_still_blocks(trades):
    from birzha.providers.continuous_futures import ContinuousFuturesEvidenceError
    source={**row('2026-01-14','RIM7','RTS',None,'2027-06-17'),'NUMTRADES':trades}
    with pytest.raises(ContinuousFuturesEvidenceError,match='volume'):
        select_completed_session_volume('RI',decision_session=date(2026,1,14),effective_session=date(2026,1,15),decision_rows=[source])


@pytest.mark.parametrize('effective,prior', [('2026-03-23','2026-03-20'),('2026-06-15','2026-06-11'),('2026-09-21','2026-09-18')])
def test_weekend_and_holiday_gaps_use_last_completed_official_session(effective,prior):
    source=row(prior,'RIZ6','RTS',100,'2026-12-17')
    client=_Client({prior:[source]})
    assert MoexHistoricalFutureResolver(client).resolve('RI',date.fromisoformat(effective)).secid=='RIZ6'
    assert all(day<effective for day in client.queries)

@pytest.mark.parametrize('civil,expected',[('2026-06-12','2026-06-15'),('2026-06-13','2026-06-15'),('2026-09-19','2026-09-21'),('2026-10-10','2026-10-12')])
def test_documented_extra_sessions_share_next_primary_trade_date(civil,expected):
    from birzha.application.forts_calendar_2026 import trade_day
    assert trade_day(date.fromisoformat(civil)).isoformat()==expected

@pytest.mark.parametrize('day',['2026-03-21','2026-09-12','2026-10-24'])
def test_published_closed_weekends_are_not_invented_sessions(day):
    from birzha.application.forts_calendar_2026 import trade_day
    with pytest.raises(ValueError,match='closed'):
        trade_day(date.fromisoformat(day))

def test_archive_contract_plan_uses_same_selector_and_never_reads_local_candles(monkeypatch):
    from types import SimpleNamespace
    from birzha.application.historical_data import HistoricalDataService
    from birzha.mcp import server
    previous=[row('2026-10-28','RIZ6','RTS',100,'2026-12-17'),row('2026-10-28','RIH7','RTS',1,'2027-03-18')]
    future=[row('2026-10-29','RIZ6','RTS',1,'2026-12-17'),row('2026-10-29','RIH7','RTS',999999,'2027-03-18')]
    resolver=MoexHistoricalFutureResolver(_Client({'2026-10-28':previous,'2026-10-29':future}))
    class History:
        market_data=SimpleNamespace(historical_future_resolver=resolver,direct_resolver=None)
        _segments=HistoricalDataService._segments
    monkeypatch.setattr(server,'_history',History())
    result=server.archive_contract_plan('RI','2026-10-29','2026-10-29')
    assert result['contracts'][0]['secid']=='RIZ6' and result['written'] is False
    assert server.archive_contract_plan('RTSI','2026-10-29','2026-10-29')['status']=='ERROR'
