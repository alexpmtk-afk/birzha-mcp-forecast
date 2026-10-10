from dataclasses import replace
from datetime import date, timedelta
import math

import pytest

from birzha.application.features import TimeframeFeatureEngine, _return_n, _sma, _efficiency_ratio, _volume_ratio
from birzha.application.normalized_features import NormalizedFeatureEngine
from birzha.application.market_state import build_market_state_vector
from birzha.application.forecast import _flow_adjustment, build_forecast_from_snapshot
from birzha.application.prediction import build_prediction_contract, FULL_WINDOWS_PREDICTION_VERSION
from birzha.application.snapshot import MarketSnapshotService
from birzha.domain.market import Candle, CandleSeries, Instrument
from birzha.domain.snapshot import MarketSnapshot, DataQualityContract, TimeframeQuality
from birzha.domain.normalized_features import NormalizedFeatureSet, NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
from birzha.domain.flow import MarketFlowSnapshot


def instrument(symbol='SBER', *, volume=True, oi=False):
    caps = ['CANDLES', 'TRADING_CALENDAR']
    if volume: caps += ['VOLUME', 'TRADESTATS']
    if oi: caps += ['OPEN_INTEREST']
    return Instrument(symbol, symbol, 'TQBR', 'stock', 'shares', 'index' if not volume else 'equity', data_capabilities=tuple(caps))


def rows(n=60, *, flat=False):
    result=[]
    for i in range(n):
        price=100.0 if flat else 100.0+i
        day=(date(2026, 1, 1)+timedelta(days=i)).isoformat()
        result.append(Candle(price, price, price+1, price-1, 1000.0, 10.0+i, day+'T10:00:00', day+'T18:00:00'))
    return result


def state(candles, timeframe='D1', inst=None):
    return TimeframeFeatureEngine().build(CandleSeries(inst or instrument(), timeframe, tuple(candles)))


def snapshot(n=21, *, inst=None, flow=None):
    inst=inst or instrument()
    states=[state(rows(n), tf, inst) for tf in ('D1','H1','M15')]
    normalized=NormalizedFeatureEngine().build(d1=states[0],h1=states[1],m15=states[2],flow=flow,volume_profile=None,instrument=inst)
    quality=DataQualityContract('TEST','DEGRADED' if n<50 else 'PASS',tuple(TimeframeQuality(tf,n,50,'2026-03-01T18:00:00','DEGRADED' if n<50 else 'PASS') for tf in ('D1','H1','M15')),'NOT_REQUESTED',tuple(f'{tf}: insufficient_history={n}' for tf in ('D1','H1','M15')) if n<50 else ())
    return MarketSnapshot(inst.symbol,inst.secid,'2026-03-01T18:00:00','TEST',*states,quality.status,(),flow=flow,quality_contract=quality,normalized_features=normalized)


@pytest.mark.parametrize('bad',[None,float('nan'),float('inf'),True,'bad',10**1000])
def test_invalid_close_never_reaches_features_or_snapshot_hash(bad):
    candles=rows()
    candles[-1]=replace(candles[-1],close=bad)
    result=state(candles)
    assert result.last_close is None
    assert result.return_5 is None and result.sma_20 is None and result.sma_50 is None
    assert result.efficiency_ratio_20 is None and result.atr_14_pct is None
    assert result.trend_score == 0


@pytest.mark.parametrize('completed',[False,None,1])
def test_unfinished_row_does_not_get_removed_or_backfilled(completed):
    candles=rows()
    candles[-3]=replace(candles[-3],completed=completed)
    result=state(candles)
    assert result.return_5 is None and result.return_20 is None
    assert result.sma_20 is None and result.atr_14_pct is None
    assert result.volume_ratio_20 is None


@pytest.mark.parametrize('n',[5,6,10,11,14,15,19,20,21,49,50])
def test_each_feature_uses_its_actual_window(n):
    result=state(rows(n))
    assert (result.return_5 is not None)==(n>=6)
    assert (result.return_10 is not None)==(n>=11)
    assert (result.return_20 is not None)==(n>=21)
    assert (result.sma_20 is not None)==(n>=20)
    assert (result.sma_50 is not None)==(n>=50)
    assert (result.atr_14_pct is not None)==(n>=15)
    assert (result.efficiency_ratio_20 is not None)==(n>=21)
    assert (result.volume_ratio_20 is not None)==(n>=21)


def test_manual_sma_atr_gap_and_efficiency_reference():
    candles=rows(15)
    candles[-1]=replace(candles[-1],high=130.,low=129.,close=130.)
    result=state(candles)
    # 13 ranges of2, then gap17 from previous close113.
    assert result.atr_14_pct == pytest.approx(((13*2+17)/14)/130)
    assert _sma([float(i) for i in range(1,21)],20)==10.5
    assert _return_n([100.,110.],1)==pytest.approx(.1)
    assert _efficiency_ratio([100.,110.,100.],2)==0
    assert _efficiency_ratio([100.,110.,120.],2)==1


def test_flat_market_does_not_invent_efficiency_or_bearish_score():
    result=state(rows(flat=True))
    assert result.return_20==0
    assert result.efficiency_ratio_20 is None
    assert result.trend_score==0
    zero_range=[replace(c,high=c.close,low=c.close) for c in rows(flat=True)]
    assert state(zero_range).atr_14_pct is None


@pytest.mark.parametrize('bad',[None,-1.,float('nan'),float('inf'),True,'bad'])
def test_bad_volume_is_not_zero_and_does_not_damage_prices(bad):
    candles=rows()
    candles[-4]=replace(candles[-4],volume=bad)
    result=state(candles)
    assert result.volume_ratio_20 is None
    assert result.return_20 is not None and result.atr_14_pct is not None


def test_observed_zero_volume_and_zero_baseline_are_distinct():
    assert _volume_ratio([10.]*20+[0.],20)==0
    assert _volume_ratio([0.]*20+[10.],20) is None
    assert _volume_ratio([10.]*20+[-1.],20) is None


@pytest.mark.parametrize('symbol,volume,oi',[('SBER',True,False),('Si',True,True),('BR',True,True),('GOLD',True,True),('IMOEX',False,False),('RTSI',False,False)])
def test_capability_matrix_preserves_price_features(symbol,volume,oi):
    inst=instrument(symbol,volume=volume,oi=oi)
    snap=snapshot(inst=inst)
    vector=build_market_state_vector(snap,inst).to_dict()
    assert vector['schema']=='MARKET_STATE_VECTOR_V1_PER_FEATURE'
    assert vector['availability']['d1_return_20_atr']['status']=='AVAILABLE'
    assert vector['features']['d1_return_20_atr'] is not None
    assert snap.d1.sma_50 is None
    assert vector['availability']['d1_volume_ratio_20']['status']==('AVAILABLE' if volume else 'NOT_APPLICABLE')
    assert vector['availability']['oi_change_ratio']['status']==('UNAVAILABLE' if oi else 'NOT_APPLICABLE')
    if not volume: assert snap.normalized_features.d1_volume_ratio_20 is None


def test_15_rows_admit_atr_and_return5_but_not_return20():
    inst=instrument()
    vector=build_market_state_vector(snapshot(15,inst=inst),inst).to_dict()
    assert vector['availability']['d1_atr_price_scale']['status']=='AVAILABLE'
    assert vector['availability']['d1_return_5_atr']['status']=='AVAILABLE'
    assert vector['availability']['d1_return_20_atr']=={'status':'INSUFFICIENT_HISTORY','reason':'D1_requires_21_candles'}


def test_unknown_quality_cannot_be_bypassed_by_feature_length():
    inst=instrument()
    snap=snapshot(inst=inst)
    snap=replace(snap,quality_contract=replace(snap.quality_contract,reasons=('UNEXPLAINED_INPUT_DEFECT',)))
    vector=build_market_state_vector(snap,inst).to_dict()
    assert vector['availability']['d1_return_20_atr']['status']=='UNAVAILABLE'


def test_legacy_market_state_retains_its_declared_50_row_guard():
    inst=instrument()
    snap=snapshot(inst=inst)
    snap=replace(snap,normalized_features=replace(snap.normalized_features,version='NORMALIZED_FEATURES_V1'))
    vector=build_market_state_vector(snap,inst).to_dict()
    assert vector['schema']=='MARKET_STATE_VECTOR_V0'
    assert vector['availability']['d1_return_20_atr']['status']=='INSUFFICIENT_HISTORY'


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,'bad'])
def test_normalization_rejects_invalid_scale(bad):
    raw=replace(state(rows()),atr_14_pct=bad)
    normalized=NormalizedFeatureEngine().build(d1=raw,h1=raw,m15=raw,flow=None,volume_profile=None)
    assert normalized.d1_atr_price_scale is None
    assert normalized.d1_return_5_atr is None
    assert normalized.distance_to_poc_atr is None


def flow():
    return MarketFlowSnapshot('TEST','TEST','2026-03-01','2026-03-01','2026-03-01T18:00:00','TEST',1,100.,50.,50.,.25,None,None,None,1.,100.,110.,10.,None,None,'PASS',())


def test_unsupported_flow_cannot_influence_new_forecast_score():
    inst=instrument('IMOEX',volume=False)
    snap=snapshot(inst=inst,flow=flow())
    assert _flow_adjustment(snap)==0
    assert build_forecast_from_snapshot(snap).direction==build_forecast_from_snapshot(replace(snap,flow=None)).direction


def test_supported_flow_stays_available_but_degraded_flow_is_closed():
    inst=instrument('Si',oi=True)
    snap=snapshot(inst=inst,flow=flow())
    assert _flow_adjustment(snap)>0
    degraded=replace(flow(),data_quality='DEGRADED',warnings=('ALGOPACK_TRADESTATS_EMPTY',))
    assert _flow_adjustment(snapshot(inst=inst,flow=degraded))==0


def test_snapshot_service_passes_capabilities_to_normalization():
    inst=instrument('IMOEX',volume=False)
    class Provider:
        def resolve(self,*args,**kwargs): return inst
        def candles_for_instrument(self,instrument,*,timeframe,**kwargs):
            candles=rows(21)
            if timeframe != 'D1':
                candles=[replace(c,begin=(date.fromisoformat(c.begin[:10])+timedelta(days=1)).isoformat()+c.begin[10:],end=(date.fromisoformat(c.end[:10])+timedelta(days=1)).isoformat()+c.end[10:]) for c in candles]
            return CandleSeries(instrument,timeframe,tuple(candles))
    snap=MarketSnapshotService(Provider(),None).build('IMOEX',as_of_date='2026-03-01')
    assert snap.normalized_features.version==NORMALIZED_FEATURES_FULL_WINDOWS_VERSION
    assert snap.normalized_features.d1_volume_ratio_20 is None
    assert build_market_state_vector(snap,inst).to_dict()['availability']['d1_return_20_atr']['status']=='AVAILABLE'


def test_new_prediction_labels_sma_honestly_without_switching_formula():
    snap=snapshot()
    new=build_prediction_contract(snap)
    legacy=build_prediction_contract(replace(snap,normalized_features=replace(snap.normalized_features,version='NORMALIZED_FEATURES_V1')))
    assert new.version==FULL_WINDOWS_PREDICTION_VERSION
    assert new.volatility_measure=='D1_SMA_TR14_PRICE_V1'
    assert new.up_barrier==legacy.up_barrier and new.down_barrier==legacy.down_barrier
    assert new.contract_id!=legacy.contract_id


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),True,'bad'])
def test_prediction_rejects_nonfinite_scale(bad):
    snap=snapshot()
    with pytest.raises(ValueError):
        build_prediction_contract(replace(snap,d1=replace(snap.d1,atr_14_pct=bad)))


def test_zero_price_level_is_not_replaced_by_older_timeframe_level():
    from birzha.application.forecast import _scenarios
    snap=snapshot()
    snap=replace(snap,h1=replace(snap.h1,support_20=0.),d1=replace(snap.d1,support_20=-5.))
    assert _scenarios(snap,'UP',snap.d1.last_close)[3]==0.


def test_negative_prices_keep_atr_scale_positive_and_price_units_consistent():
    candles=[replace(c,open=c.open-200,high=c.high-200,low=c.low-200,close=c.close-200) for c in rows(21)]
    raw=state(candles)
    assert raw.atr_14_pct > 0
    result=NormalizedFeatureEngine().build(d1=raw,h1=raw,m15=raw,flow=None,volume_profile=None)
    assert result.d1_atr_price_scale==2.


def test_malformed_nonfinite_snapshot_is_rejected_without_rewriting_identity():
    inst=instrument()
    snap=snapshot(60,inst=inst)
    snap=replace(snap,normalized_features=replace(snap.normalized_features,d1_return_20_atr=float('nan')))
    # An invalid snapshot cannot receive a canonical identity; do not sanitize
    # it silently under the original identifier.
    with pytest.raises(ValueError):
        build_market_state_vector(snap,inst)


def test_new_forecast_rules_have_a_distinct_version_and_preserve_legacy_version():
    from birzha.application.forecast import ENGINE_VERSION, FULL_WINDOWS_ENGINE_VERSION
    snap=snapshot()
    assert build_forecast_from_snapshot(snap).engine_version==FULL_WINDOWS_ENGINE_VERSION
    old=replace(snap,normalized_features=replace(snap.normalized_features,version='NORMALIZED_FEATURES_V1'))
    assert build_forecast_from_snapshot(old).engine_version==ENGINE_VERSION
