from dataclasses import replace
import pytest
from test_production_feature_contracts import snapshot, instrument, rows
from birzha.application.price_levels import build_price_level_evidence
from birzha.application.forecast import build_forecast_from_snapshot
from birzha.application.volume_profile import profile_from_candles
from birzha.application.normalized_features import NormalizedFeatureEngine
from birzha.domain.market import CandleSeries
from birzha.domain.forecast import BASELINE_EVIDENCE_RECORD_VERSION, LEVEL_EVIDENCE_RECORD_VERSION, validate_baseline_evidence_payload
from birzha.storage.forecast_journal import _record_from_dict


def with_profile(n=60):
    snap = snapshot(n)
    profile = profile_from_candles(CandleSeries(instrument(), 'H1', tuple(rows(n))))
    normalized = NormalizedFeatureEngine().build(d1=snap.d1,h1=snap.h1,m15=snap.m15,volume_profile=profile,flow=None,instrument=instrument())
    return replace(snap,volume_profile=profile,normalized_features=normalized)


def test_origins_preserve_coincident_levels_and_snapshot_identity():
    record = build_forecast_from_snapshot(snapshot())
    assert record.record_version == LEVEL_EVIDENCE_RECORD_VERSION
    assert len(record.level_evidence) == 9
    assert len([i for i in record.level_evidence if i.price is not None]) == 6
    assert len(record.key_levels) == 2
    assert all(i.snapshot_id == record.snapshot_id and i.strength == 'UNKNOWN' for i in record.level_evidence)
    assert _record_from_dict(record.to_dict()).to_dict() == record.to_dict()


@pytest.mark.parametrize('n',[0,1,19,20,21,49,50,60])
def test_range_requires_twenty_complete_rows(n):
    levels = build_price_level_evidence(snapshot(n))
    assert all((i.price is not None) == (n >= 20) for i in levels[:6])


@pytest.mark.parametrize('end',['2026-03-02T18:00:00','garbage',None,'2026-03-01'])
def test_unknown_or_future_end_closes_only_affected_timeframe(end):
    snap=snapshot(60)
    quality=replace(snap.quality_contract,timeframes=tuple(replace(q,latest_completed_end=end) if q.timeframe=='H1' else q for q in snap.quality_contract.timeframes))
    levels=build_price_level_evidence(replace(snap,quality_contract=quality))
    assert all(i.price is None and i.reason for i in levels[2:4])
    assert all(i.price is not None for i in levels[:2]+levels[4:6])


def test_zero_and_negative_bounds_and_precision_are_preserved():
    snap=snapshot(60)
    snap=replace(snap,d1=replace(snap.d1,support_20=-1.123456789123,resistance_20=0.))
    record=build_forecast_from_snapshot(snap)
    assert -1.123456789123 in record.key_levels and 0. in record.key_levels
    record.to_dict()


@pytest.mark.parametrize('change',[{'support_20':None},{'support_20':1000.},{'timeframe':'H1'}])
def test_invalid_range_pair_closed(change):
    snap=snapshot(60)
    levels=build_price_level_evidence(replace(snap,d1=replace(snap.d1,**change)))
    assert all(i.price is None for i in levels[:2])


@pytest.mark.parametrize('n',[49,50,60])
def test_actual_candle_proxy_method_is_accepted_and_marked_approximate(n):
    snap=with_profile(n)
    levels=build_price_level_evidence(snap)[-3:]
    assert all((i.status=='AVAILABLE_APPROXIMATE') == (n>=50) for i in levels)
    record=build_forecast_from_snapshot(snap)
    assert ('optional_profile_excluded' in record.reasons) == (n<50)
    record.to_dict()


def test_unknown_profile_method_and_missing_capability_are_excluded():
    snap=with_profile()
    snap=replace(snap,volume_profile=replace(snap.volume_profile,method='UNKNOWN'))
    assert all(i.price is None for i in build_price_level_evidence(snap)[-3:])
    snap=with_profile()
    snap=replace(snap,normalized_features=replace(snap.normalized_features,distance_to_poc_atr=None))
    assert all(i.price is None for i in build_price_level_evidence(snap)[-3:])


@pytest.mark.parametrize('change',[{'strength':'STRONG'},{'snapshot_id':'other'},{'source_end':'2026-03-02T18:00:00'},{'price':None},{'status':'AVAILABLE_APPROXIMATE'}])
def test_serialized_fabrications_rejected(change):
    payload=build_forecast_from_snapshot(snapshot()).to_dict()
    payload['level_evidence'][0].update(change)
    with pytest.raises(ValueError): validate_baseline_evidence_payload(payload)


def test_v2_payload_roundtrip_shape_remains_unchanged():
    record=replace(build_forecast_from_snapshot(snapshot()),record_version=BASELINE_EVIDENCE_RECORD_VERSION)
    payload=record.to_dict()
    assert 'level_evidence' not in payload and 'level_evidence_version' not in payload
    assert _record_from_dict(payload).to_dict() == payload


@pytest.mark.parametrize('mode',['duplicate','count','degraded','legacy'])
def test_unproven_quality_or_producer_cannot_certify_levels(mode):
    snap=snapshot(60)
    if mode=='legacy':
        snap=replace(snap,normalized_features=replace(snap.normalized_features,version='NORMALIZED_FEATURES_V1'))
    else:
        qs=snap.quality_contract.timeframes
        if mode=='duplicate': qs=qs+(qs[0],)
        if mode=='count': qs=(replace(qs[0],candles=59),*qs[1:])
        if mode=='degraded': qs=(replace(qs[0],status='DEGRADED'),*qs[1:])
        snap=replace(snap,quality_contract=replace(snap.quality_contract,timeframes=qs))
    assert all(i.price is None for i in build_price_level_evidence(snap)[:2])


def exact_profile():
    from birzha.domain.flow import MarketFlowSnapshot
    snap=with_profile()
    profile=replace(snap.volume_profile,method='PUBLIC_TRADES_PRICE_QUANTITY_V1')
    flow=MarketFlowSnapshot(snap.symbol,snap.secid,'2026-03-01','2026-03-01',snap.as_of,'PUBLIC_TRADES',1,None,None,None,None,None,None,None,None,None,None,None,None,None,'PASS',(),volume_profile=profile)
    normalized=replace(snap.normalized_features,profile_method=profile.method,profile_is_exact=True)
    return replace(snap,flow=flow,volume_profile=profile,normalized_features=normalized)


def test_exact_profile_requires_matching_flow_source_and_time():
    snap=exact_profile()
    assert all(i.status=='AVAILABLE' and i.timeframe is None for i in build_price_level_evidence(snap)[-3:])
    build_forecast_from_snapshot(snap).to_dict()
    for change in ({'secid':'other'},{'volume_profile':None},{'as_of':'2026-03-02T18:00:00'},{'data_quality':'DEGRADED'},{'warnings':('UNKNOWN',)}):
        invalid=replace(snap,flow=replace(snap.flow,**change))
        assert all(i.price is None for i in build_price_level_evidence(invalid)[-3:])
        assert 'optional_profile_excluded' in build_forecast_from_snapshot(invalid).reasons


def test_prospective_capture_refuses_tampered_level_origin_before_writing(tmp_path):
    from birzha.application.prospective_capture import ProspectivePilotLedger, AdmissionRefused
    from test_prospective_capture import BASE, source
    payload=build_forecast_from_snapshot(snapshot()).to_dict()
    payload['level_evidence'][0]['snapshot_id']='other'
    class Record:
        def to_dict(self): return payload
    ledger=ProspectivePilotLedger(tmp_path/'levels.sqlite3',clock=lambda:BASE)
    try:
        with pytest.raises(AdmissionRefused): ledger.capture(Record(),source())
        assert ledger.audit()['captures']==0
    finally: ledger.close()
